from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, AsyncContextManager
from urllib.parse import urljoin

import aiohttp

from app.core.anthropic.models import AnthropicMessageRequest, AnthropicUsage, merge_usage_values
from app.core.anthropic.parsing import parse_sse_event
from app.core.auth.refresh import RefreshError
from app.core.balancer.types import UpstreamError
from app.core.clients.http import lease_http_session
from app.core.clients.proxy import filter_inbound_headers
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.providers import ANTHROPIC_PROVIDER_NAME
from app.core.utils.request_id import ensure_request_id
from app.db.models import Account
from app.modules.accounts.auth_manager import AuthManager
from app.modules.api_keys.service import ApiKeyData, ApiKeysService, ApiKeyUsageReservationData
from app.modules.proxy.load_balancer import LoadBalancer
from app.modules.proxy.repo_bundle import ProxyRepoFactory

logger = logging.getLogger(__name__)

_STREAM_CHUNK_SIZE = 8192
_MAX_SELECTION_ATTEMPTS = 4


@dataclass(frozen=True, slots=True)
class AnthropicProxyStream:
    body: AsyncIterator[bytes]
    media_type: str


class AnthropicProxyError(Exception):
    def __init__(self, status_code: int, message: str, *, code: str = "anthropic_proxy_error") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code


class AnthropicProxyService:
    def __init__(self, repo_factory: ProxyRepoFactory) -> None:
        self._repo_factory = repo_factory
        self._load_balancer = LoadBalancer(repo_factory)
        self._encryptor = TokenEncryptor()

    async def stream_messages(
        self,
        payload: AnthropicMessageRequest,
        inbound_headers: Mapping[str, str],
        *,
        api_key: ApiKeyData | None = None,
        api_key_reservation: ApiKeyUsageReservationData | None = None,
    ) -> AnthropicProxyStream:
        request_id = ensure_request_id(_request_id_from_headers(inbound_headers))
        started_at = time.monotonic()
        selected_account_ids: set[str] = set()
        media_type = "text/event-stream" if payload.stream else "application/json"

        async def body() -> AsyncIterator[bytes]:
            usage: AnthropicUsage | None = None
            last_account: Account | None = None
            last_error_status: int | None = None
            last_error_message: str | None = None
            for _ in range(_MAX_SELECTION_ATTEMPTS):
                account = await self._select_account(payload.model, exclude_account_ids=selected_account_ids)
                selected_account_ids.add(account.id)
                last_account = account
                access_token = await self._fresh_access_token(account)
                headers = _build_anthropic_headers(inbound_headers, access_token)
                body_payload = payload.model_dump(mode="json", exclude_none=True)

                async with lease_http_session() as session:
                    async with self._open_upstream_response(session, headers=headers, json_body=body_payload) as resp:
                        if resp.status in {401, 403}:
                            error_message = await _read_error_message(resp)
                            await self._load_balancer.mark_permanent_failure(account, "invalid_api_key")
                            await self._persist_request_log(
                                account=account,
                                request_id=request_id,
                                model=payload.model,
                                started_at=started_at,
                                status="error",
                                error_code="invalid_api_key",
                                error_message=error_message,
                                api_key=api_key,
                            )
                            last_error_status = resp.status
                            last_error_message = error_message
                            continue
                        if resp.status == 429:
                            error_message = await _read_error_message(resp)
                            await self._load_balancer.mark_rate_limit(
                                account,
                                UpstreamError(message=error_message),
                            )
                            await self._persist_request_log(
                                account=account,
                                request_id=request_id,
                                model=payload.model,
                                started_at=started_at,
                                status="error",
                                error_code="rate_limit_exceeded",
                                error_message=error_message,
                                api_key=api_key,
                            )
                            last_error_status = resp.status
                            last_error_message = error_message
                            continue
                        if resp.status >= 400:
                            error_message = await _read_error_message(resp)
                            await self._load_balancer.record_error(account)
                            await self._persist_request_log(
                                account=account,
                                request_id=request_id,
                                model=payload.model,
                                started_at=started_at,
                                status="error",
                                error_code=f"upstream_{resp.status}",
                                error_message=error_message,
                                api_key=api_key,
                            )
                            raise AnthropicProxyError(resp.status, error_message, code=f"upstream_{resp.status}")

                        text_buffer = ""
                        async for chunk in resp.content.iter_chunked(_STREAM_CHUNK_SIZE):
                            if not chunk:
                                continue
                            text_buffer, usage = _collect_usage_from_chunk(text_buffer, bytes(chunk), usage)
                            yield bytes(chunk)

                        await self._load_balancer.record_success(account)
                        await self._persist_request_log(
                            account=account,
                            request_id=request_id,
                            model=payload.model,
                            started_at=started_at,
                            status="success",
                            api_key=api_key,
                            usage=usage,
                        )
                        await self._finalize_api_key_reservation(
                            api_key_reservation,
                            model=payload.model,
                            usage=usage,
                        )
                        return

            await self._release_api_key_reservation(api_key_reservation)
            message = last_error_message or "No available Anthropic accounts"
            await self._persist_request_log(
                account=last_account,
                request_id=request_id,
                model=payload.model,
                started_at=started_at,
                status="error",
                error_code="no_available_anthropic_accounts",
                error_message=message,
                api_key=api_key,
            )
            raise AnthropicProxyError(last_error_status or 503, message, code="no_available_anthropic_accounts")

        return AnthropicProxyStream(body=body(), media_type=media_type)

    def _open_upstream_response(
        self,
        session: aiohttp.ClientSession,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any],
    ) -> AsyncContextManager[aiohttp.ClientResponse]:
        settings = get_settings()
        url = urljoin(settings.anthropic_upstream_base_url.rstrip("/") + "/", "v1/messages")
        timeout = aiohttp.ClientTimeout(
            total=settings.proxy_request_budget_seconds,
            connect=settings.upstream_connect_timeout_seconds,
        )
        return session.post(url, json=json_body, headers=headers, timeout=timeout)

    async def _select_account(self, model: str, *, exclude_account_ids: set[str]) -> Account:
        selection = await self._load_balancer.select_account(
            model=model,
            provider=ANTHROPIC_PROVIDER_NAME,
            exclude_account_ids=exclude_account_ids,
        )
        if selection.account is None:
            raise AnthropicProxyError(
                503,
                selection.error_message or "No available Anthropic accounts",
                code=selection.error_code or "no_available_anthropic_accounts",
            )
        return selection.account

    async def _fresh_access_token(self, account: Account) -> str:
        try:
            async with self._repo_factory() as repos:
                latest = await repos.accounts.get_by_id(account.id)
                if latest is None:
                    raise AnthropicProxyError(503, "Selected Anthropic account no longer exists")
                manager = AuthManager(repos.accounts)
                fresh = await manager.ensure_fresh(latest)
                return self._encryptor.decrypt(fresh.access_token_encrypted)
        except RefreshError as exc:
            if exc.is_permanent:
                await self._load_balancer.mark_permanent_failure(account, exc.code)
            raise AnthropicProxyError(401, exc.message, code=exc.code) from exc

    async def _persist_request_log(
        self,
        *,
        account: Account | None,
        request_id: str,
        model: str,
        started_at: float,
        status: str,
        api_key: ApiKeyData | None,
        error_code: str | None = None,
        error_message: str | None = None,
        usage: AnthropicUsage | None = None,
    ) -> None:
        try:
            async with self._repo_factory() as repos:
                await repos.request_logs.add_log(
                    account_id=account.id if account else None,
                    api_key_id=api_key.id if api_key else None,
                    request_id=request_id,
                    model=model,
                    input_tokens=usage.input_tokens if usage else None,
                    output_tokens=usage.output_tokens if usage else None,
                    cached_input_tokens=usage.cache_read_input_tokens if usage else None,
                    cache_creation_tokens=usage.cache_creation_input_tokens if usage else None,
                    cache_read_tokens=usage.cache_read_input_tokens if usage else None,
                    latency_ms=int((time.monotonic() - started_at) * 1000),
                    status=status,
                    error_code=error_code,
                    error_message=error_message,
                    plan_type=account.plan_type if account else None,
                    provider=ANTHROPIC_PROVIDER_NAME,
                    transport="http",
                )
        except Exception:
            logger.warning("Failed to persist Anthropic request log request_id=%s", request_id, exc_info=True)

    async def _finalize_api_key_reservation(
        self,
        reservation: ApiKeyUsageReservationData | None,
        *,
        model: str,
        usage: AnthropicUsage | None,
    ) -> None:
        if reservation is None:
            return
        if usage is None:
            await self._release_api_key_reservation(reservation)
            return
        async with self._repo_factory() as repos:
            service = ApiKeysService(repos.api_keys)
            cached_tokens = (usage.cache_creation_input_tokens or 0) + (usage.cache_read_input_tokens or 0)
            await service.finalize_usage_reservation(
                reservation.reservation_id,
                model=model,
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cached_input_tokens=cached_tokens,
            )

    async def _release_api_key_reservation(self, reservation: ApiKeyUsageReservationData | None) -> None:
        if reservation is None:
            return
        async with self._repo_factory() as repos:
            service = ApiKeysService(repos.api_keys)
            await service.release_usage_reservation(reservation.reservation_id)


def _request_id_from_headers(headers: Mapping[str, str]) -> str | None:
    return headers.get("x-request-id") or headers.get("request-id")


def _build_anthropic_headers(inbound_headers: Mapping[str, str], access_token: str) -> dict[str, str]:
    settings = get_settings()
    headers = filter_inbound_headers(inbound_headers)
    lower_keys = {key.lower() for key in headers}
    headers["Authorization"] = f"Bearer {access_token}"
    if "anthropic-version" not in lower_keys:
        headers["anthropic-version"] = settings.anthropic_version
    if "content-type" not in lower_keys:
        headers["content-type"] = "application/json"
    if "accept" not in lower_keys:
        headers["accept"] = "text/event-stream"
    return headers


def _collect_usage_from_chunk(
    text_buffer: str,
    chunk: bytes,
    usage: AnthropicUsage | None,
) -> tuple[str, AnthropicUsage | None]:
    text_buffer += chunk.decode("utf-8", errors="replace")
    normalized = text_buffer.replace("\r\n", "\n")
    while "\n\n" in normalized:
        block, normalized = normalized.split("\n\n", 1)
        event = parse_sse_event(block)
        if event is None:
            continue
        event_usage = getattr(event, "usage", None)
        if event_usage is None and getattr(event, "message", None) is not None:
            event_usage = event.message.usage
        usage = merge_usage_values(usage, event_usage)
    return normalized, usage


async def _read_error_message(resp: aiohttp.ClientResponse) -> str:
    raw = await resp.read()
    if not raw:
        return f"Anthropic upstream returned HTTP {resp.status}"
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw.decode("utf-8", errors="replace")
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        if isinstance(payload.get("message"), str):
            return payload["message"]
    return f"Anthropic upstream returned HTTP {resp.status}"
