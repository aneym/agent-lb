from __future__ import annotations

import json
import logging
import time
from collections import Counter
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any, AsyncContextManager
from urllib.parse import urljoin

import aiohttp
from pydantic import ValidationError

from app.core.anthropic.models import AnthropicMessageRequest, AnthropicUsage, merge_usage_values
from app.core.anthropic.parsing import parse_sse_event
from app.core.auth.refresh import RefreshError
from app.core.balancer.types import UpstreamError
from app.core.clients.http import lease_http_session
from app.core.clients.proxy import filter_inbound_headers
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.crypto import TokenEncryptor
from app.core.providers import ANTHROPIC_PROVIDER_NAME, GLM_PROVIDER_NAME
from app.core.utils.request_id import ensure_request_id
from app.db.models import Account, StickySessionKind
from app.modules.accounts.auth_manager import AuthManager
from app.modules.api_keys.service import ApiKeyData, ApiKeysService, ApiKeyUsageReservationData
from app.modules.proxy.load_balancer import LoadBalancer, selectable_accounts
from app.modules.proxy.repo_bundle import ProxyRepoFactory

logger = logging.getLogger(__name__)

_STREAM_CHUNK_SIZE = 8192
_MAX_SELECTION_ATTEMPTS = 4
_ANTHROPIC_COOLDOWN_WINDOW = "primary"
_ANTHROPIC_COOLDOWN_FEATURE = "anthropic_messages"
_ANTHROPIC_DEFAULT_COOLDOWN_SECONDS = 60
_ANTHROPIC_FAST_QUOTA_KEY = "anthropic_fast"
_ANTHROPIC_OAUTH_BETA = "oauth-2025-04-20"
_ANTHROPIC_FAST_MODE_BETA = "fast-mode-2026-02-01"


@dataclass(frozen=True, slots=True)
class AnthropicProxyStream:
    body: AsyncIterator[bytes]
    media_type: str


@dataclass(frozen=True, slots=True)
class _AnthropicQuotaEligibility:
    account_ids: list[str]
    blocked_count: int = 0
    next_reset_at: int | None = None


@dataclass(frozen=True, slots=True)
class _AnthropicErrorDetails:
    message: str
    error_type: str | None = None


class AnthropicProxyError(Exception):
    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        code: str = "anthropic_proxy_error",
        retry_at: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.retry_at = retry_at


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
        provider_name = _messages_provider_name(payload)
        quota_key = _messages_quota_key(payload, provider_name=provider_name)
        affinity_quota_key = _messages_affinity_quota_key(payload, provider_name=provider_name)
        sticky_key = _messages_sticky_key(
            payload,
            inbound_headers,
            provider_name=provider_name,
            quota_key=affinity_quota_key,
        )
        first_account = await self._select_account(
            payload.model,
            provider_name=provider_name,
            exclude_account_ids=selected_account_ids,
            sticky_key=sticky_key,
            quota_key=quota_key,
        )
        selected_account_ids.add(first_account.id)

        async def body() -> AsyncIterator[bytes]:
            usage: AnthropicUsage | None = None
            last_account: Account | None = None
            last_error_status: int | None = None
            last_error_message: str | None = None
            for attempt in range(_MAX_SELECTION_ATTEMPTS):
                if attempt == 0:
                    account = first_account
                else:
                    account = await self._select_account(
                        payload.model,
                        provider_name=provider_name,
                        exclude_account_ids=selected_account_ids,
                        sticky_key=sticky_key,
                        quota_key=quota_key,
                    )
                    selected_account_ids.add(account.id)
                last_account = account
                access_token = await self._fresh_access_token(account)
                headers = _build_anthropic_headers(
                    inbound_headers,
                    access_token,
                    provider_name=provider_name,
                    fast_mode=_anthropic_fast_mode_requested(payload),
                )
                body_payload = payload.model_dump(mode="json", exclude_none=True)

                async with lease_http_session() as session:
                    async with self._open_upstream_response(
                        session,
                        provider_name=provider_name,
                        headers=headers,
                        json_body=body_payload,
                    ) as resp:
                        if resp.status in {401, 403}:
                            error_message = await _read_error_message(resp)
                            await self._load_balancer.mark_permanent_failure(account, "invalid_api_key")
                            await self._persist_request_log(
                                account=account,
                                provider_name=provider_name,
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
                            await self._record_quota_cooldown(
                                account,
                                quota_key=quota_key,
                                error=_rate_limit_error_from_response(resp, error_message),
                            )
                            await self._persist_request_log(
                                account=account,
                                provider_name=provider_name,
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
                            error_details = await _read_error_details(resp)
                            await self._load_balancer.record_error(account)
                            await self._persist_request_log(
                                account=account,
                                provider_name=provider_name,
                                request_id=request_id,
                                model=payload.model,
                                started_at=started_at,
                                status="error",
                                error_code=f"upstream_{resp.status}",
                                error_message=error_details.message,
                                api_key=api_key,
                            )
                            raise AnthropicProxyError(
                                resp.status,
                                error_details.message,
                                code=error_details.error_type or _anthropic_error_type_for_status(resp.status),
                            )

                        text_buffer = ""
                        # Non-streaming responses are a single JSON document, not SSE, so the
                        # SSE collector never sees usage. Buffer the raw body and parse it at the end.
                        raw_body = bytearray() if not payload.stream else None
                        async for chunk in resp.content.iter_chunked(_STREAM_CHUNK_SIZE):
                            if not chunk:
                                continue
                            chunk_bytes = bytes(chunk)
                            if raw_body is not None:
                                raw_body.extend(chunk_bytes)
                            else:
                                text_buffer, usage = _collect_usage_from_chunk(text_buffer, chunk_bytes, usage)
                            yield chunk_bytes
                        if raw_body is not None:
                            usage = _usage_from_json_body(bytes(raw_body)) or usage

                        await self._load_balancer.record_success(account)
                        await self._clear_quota_cooldown(account, quota_key=quota_key)
                        await self._persist_request_log(
                            account=account,
                            provider_name=provider_name,
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
            message = last_error_message or f"No available {_provider_label(provider_name)} accounts"
            await self._persist_request_log(
                account=last_account,
                provider_name=provider_name,
                request_id=request_id,
                model=payload.model,
                started_at=started_at,
                status="error",
                error_code=_no_available_accounts_code(provider_name),
                error_message=message,
                api_key=api_key,
            )
            # Upstream 429s above recorded cooldowns, so eligibility now knows the
            # earliest reset; surface it so clients can schedule a retry.
            eligibility = await self._provider_quota_eligibility(provider_name, quota_key)
            raise AnthropicProxyError(
                last_error_status or 503,
                message,
                code=_no_available_accounts_code(provider_name),
                retry_at=eligibility.next_reset_at,
            )

        return AnthropicProxyStream(body=body(), media_type=media_type)

    def _open_upstream_response(
        self,
        session: aiohttp.ClientSession,
        *,
        provider_name: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any],
    ) -> AsyncContextManager[aiohttp.ClientResponse]:
        settings = get_settings()
        base_url = (
            settings.glm_anthropic_upstream_base_url
            if provider_name == GLM_PROVIDER_NAME
            else settings.anthropic_upstream_base_url
        )
        url = urljoin(base_url.rstrip("/") + "/", "v1/messages")
        timeout = aiohttp.ClientTimeout(
            total=settings.proxy_request_budget_seconds,
            connect=settings.upstream_connect_timeout_seconds,
        )
        return session.post(url, json=json_body, headers=headers, timeout=timeout)

    async def _select_account(
        self,
        model: str,
        *,
        provider_name: str,
        exclude_account_ids: set[str],
        sticky_key: str | None,
        quota_key: str,
    ) -> Account:
        eligibility = await self._provider_quota_eligibility(provider_name, quota_key)
        if not eligibility.account_ids and eligibility.blocked_count > 0:
            reset_suffix = (
                f" Reset at {datetime.fromtimestamp(eligibility.next_reset_at).isoformat()}."
                if eligibility.next_reset_at is not None
                else ""
            )
            raise AnthropicProxyError(
                429,
                (
                    f"All {_provider_label(provider_name)} accounts are cooling down "
                    f"for quota '{quota_key}'.{reset_suffix}"
                ),
                code=_quota_cooldown_code(provider_name),
                retry_at=eligibility.next_reset_at,
            )
        settings = await get_settings_cache().get()
        selection = await self._load_balancer.select_account(
            model=model,
            provider=provider_name,
            account_ids=eligibility.account_ids,
            exclude_account_ids=exclude_account_ids,
            sticky_key=sticky_key,
            sticky_kind=StickySessionKind.CODEX_SESSION if sticky_key else None,
            prefer_earlier_reset_accounts=settings.prefer_earlier_reset_accounts,
            prefer_earlier_reset_window="primary",
            routing_strategy="usage_weighted",
        )
        if selection.account is None:
            message, retry_at = await self._selection_failure_details(
                model=model,
                quota_key=quota_key,
                provider_name=provider_name,
                fallback=selection.error_message or f"No available {_provider_label(provider_name)} accounts",
                quota_reset_at=eligibility.next_reset_at,
                quota_blocked_count=eligibility.blocked_count,
                quota_candidate_count=len(eligibility.account_ids),
                selection_error_code=selection.error_code,
                selection_error_message=selection.error_message,
            )
            raise AnthropicProxyError(
                503,
                message,
                code=selection.error_code or _no_available_accounts_code(provider_name),
                retry_at=retry_at,
            )
        return selection.account

    async def _selection_failure_details(
        self,
        *,
        model: str,
        quota_key: str,
        provider_name: str,
        fallback: str,
        quota_reset_at: int | None = None,
        quota_blocked_count: int = 0,
        quota_candidate_count: int | None = None,
        selection_error_code: str | None = None,
        selection_error_message: str | None = None,
    ) -> tuple[str, int | None]:
        now = int(time.time())
        reset_candidates: list[int] = [quota_reset_at] if quota_reset_at is not None else []
        async with self._repo_factory() as repos:
            provider_accounts = [
                account
                for account in await repos.accounts.list_accounts()
                if account.provider.lower() == provider_name
            ]
            # Headline counts and status summary reflect only the routable
            # pool — counting canceled/deactivated rows would inflate the
            # total and imply spare capacity that can never serve traffic.
            accounts = selectable_accounts(provider_accounts)
            unusable_count = len(provider_accounts) - len(accounts)
            account_count = len(accounts)
            status_counts = Counter(_account_status_label(account) for account in accounts)
            reset_candidates.extend(int(account.reset_at) for account in accounts if account.reset_at)
            primary_usage = await repos.usage.latest_by_account(
                "primary",
                account_ids=[account.id for account in accounts],
            )
            reset_candidates.extend(
                int(entry.reset_at)
                for entry in primary_usage.values()
                if entry.reset_at and float(entry.used_percent) >= 100.0
            )
        future_resets = [candidate for candidate in reset_candidates if candidate > now]
        retry_at = min(future_resets) if future_resets else None
        if account_count == 0:
            return fallback, retry_at
        status_summary = ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
        noun = "account" if account_count == 1 else "accounts"
        reset_suffix = (
            f" Limits reset at {datetime.fromtimestamp(retry_at).isoformat()}." if retry_at is not None else ""
        )
        quota_detail = _anthropic_selection_quota_detail(
            quota_key=quota_key,
            blocked_count=quota_blocked_count,
            candidate_count=quota_candidate_count,
            selection_error_code=selection_error_code,
            selection_error_message=selection_error_message,
            retry_at=retry_at,
        )
        quota_sentence = f"Model quota: {quota_detail}. " if quota_detail else ""
        stored_note = (
            f" (+{unusable_count} stored but not routable: canceled, deactivated, paused, or reauth-required)"
            if unusable_count
            else ""
        )
        message = (
            f"{account_count} {_provider_label(provider_name)} {noun} exist, but none are selectable for "
            f"{model}/{quota_key}; statuses: {status_summary}.{stored_note} "
            f"{quota_sentence}"
            f"{_other_provider_routing_message(provider_name)}{reset_suffix}"
        )
        logger.warning(
            (
                "Anthropic account selection failed model=%s quota_key=%s statuses=%s "
                "quota_blocked=%s quota_candidates=%s selection_error_code=%s retry_at=%s"
            ),
            model,
            quota_key,
            status_summary,
            quota_blocked_count,
            quota_candidate_count,
            selection_error_code,
            retry_at,
        )
        return message, retry_at

    async def _provider_quota_eligibility(self, provider_name: str, quota_key: str) -> _AnthropicQuotaEligibility:
        now = int(time.time())
        async with self._repo_factory() as repos:
            provider_accounts = [
                account
                for account in await repos.accounts.list_accounts()
                if account.provider.lower() == provider_name
            ]
            # Scope eligibility to the same routable pool the load balancer
            # uses. Canceled-subscription, deactivated, paused, and
            # reauth-required rows can never be selected, so counting them
            # as "remaining candidates" produces misleading diagnostics and
            # masks the real "all usable accounts are cooling down" state.
            account_ids = [account.id for account in selectable_accounts(provider_accounts)]
            latest = await repos.additional_usage.latest_by_account(
                quota_key,
                _ANTHROPIC_COOLDOWN_WINDOW,
                account_ids=account_ids,
            )
            cooldowns = {
                account_id: (float(entry.used_percent), entry.reset_at) for account_id, entry in latest.items()
            }

        eligible_account_ids: list[str] = []
        reset_candidates: list[int] = []
        blocked_count = 0
        for account_id in account_ids:
            cooldown = cooldowns.get(account_id)
            if cooldown is not None and _anthropic_cooldown_is_active(cooldown[0], cooldown[1], now=now):
                blocked_count += 1
                if cooldown[1] is not None:
                    reset_candidates.append(int(cooldown[1]))
                continue
            eligible_account_ids.append(account_id)
        return _AnthropicQuotaEligibility(
            account_ids=eligible_account_ids,
            blocked_count=blocked_count,
            next_reset_at=min(reset_candidates) if reset_candidates else None,
        )

    async def _record_quota_cooldown(self, account: Account, *, quota_key: str, error: UpstreamError) -> None:
        reset_at = _reset_at_from_rate_limit_error(error)
        now = int(time.time())
        window_minutes = max(1, int((reset_at - now + 59) / 60)) if reset_at is not None else None
        try:
            async with self._repo_factory() as repos:
                await repos.additional_usage.add_entry(
                    account.id,
                    limit_name=quota_key,
                    metered_feature=_ANTHROPIC_COOLDOWN_FEATURE,
                    quota_key=quota_key,
                    window=_ANTHROPIC_COOLDOWN_WINDOW,
                    used_percent=100.0,
                    reset_at=reset_at,
                    window_minutes=window_minutes,
                )
        except Exception:
            logger.warning(
                "Failed to persist Anthropic quota cooldown account_id=%s quota_key=%s",
                account.id,
                quota_key,
                exc_info=True,
            )

    async def _clear_quota_cooldown(self, account: Account, *, quota_key: str) -> None:
        try:
            async with self._repo_factory() as repos:
                await repos.additional_usage.add_entry(
                    account.id,
                    limit_name=quota_key,
                    metered_feature=_ANTHROPIC_COOLDOWN_FEATURE,
                    quota_key=quota_key,
                    window=_ANTHROPIC_COOLDOWN_WINDOW,
                    used_percent=0.0,
                    reset_at=None,
                    window_minutes=None,
                )
        except Exception:
            logger.warning(
                "Failed to clear Anthropic quota cooldown account_id=%s quota_key=%s",
                account.id,
                quota_key,
                exc_info=True,
            )

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
        provider_name: str,
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
                    provider=provider_name,
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

    async def claim_session_route(
        self,
        *,
        session_id: str,
        model: str,
        quota_key: str,
        affinity_quota_key: str | None = None,
        provider_name: str = ANTHROPIC_PROVIDER_NAME,
    ) -> Account:
        sticky_quota_key = affinity_quota_key or quota_key
        sticky_key = f"{_sticky_prefix(provider_name)}:{sticky_quota_key}:session:{_hash_for_key(session_id)}"
        account = await self._select_account(
            model,
            provider_name=provider_name,
            exclude_account_ids=set(),
            sticky_key=sticky_key,
            quota_key=quota_key,
        )
        async with self._repo_factory() as repos:
            await repos.sticky_sessions.upsert(sticky_key, account.id, kind=StickySessionKind.CODEX_SESSION)
        return account


def _request_id_from_headers(headers: Mapping[str, str]) -> str | None:
    return headers.get("x-request-id") or headers.get("request-id")


def _anthropic_quota_key(payload: AnthropicMessageRequest) -> str:
    model = payload.model.lower()
    if "haiku" in model:
        return "anthropic_standard"
    if payload.thinking:
        return "anthropic_top_thinking"
    return "anthropic_top"


def _messages_provider_name(payload: AnthropicMessageRequest) -> str:
    model = payload.model.strip().lower()
    return GLM_PROVIDER_NAME if model.startswith("glm-") else ANTHROPIC_PROVIDER_NAME


def _messages_quota_key(payload: AnthropicMessageRequest, *, provider_name: str) -> str:
    if provider_name == GLM_PROVIDER_NAME:
        return "glm_coding_thinking" if payload.thinking else "glm_coding"
    if _anthropic_fast_mode_requested(payload):
        return _ANTHROPIC_FAST_QUOTA_KEY
    return _anthropic_quota_key(payload)


def _messages_affinity_quota_key(payload: AnthropicMessageRequest, *, provider_name: str) -> str:
    if provider_name == GLM_PROVIDER_NAME:
        return "glm_coding_thinking" if payload.thinking else "glm_coding"
    return _anthropic_quota_key(payload)


def _anthropic_fast_mode_requested(payload: AnthropicMessageRequest) -> bool:
    speed = payload.speed
    return isinstance(speed, str) and speed.strip().lower() == "fast"


def _messages_sticky_key(
    payload: AnthropicMessageRequest,
    headers: Mapping[str, str],
    *,
    provider_name: str,
    quota_key: str,
) -> str | None:
    header_value = _anthropic_session_header(headers)
    prefix = _sticky_prefix(provider_name)
    if header_value:
        return f"{prefix}:{quota_key}:session:{_hash_for_key(header_value)}"
    derived = _derive_anthropic_session_material(payload)
    if not derived:
        return None
    return f"{prefix}:{quota_key}:derived:{_hash_for_key(derived)}"


def _anthropic_sticky_key(
    payload: AnthropicMessageRequest,
    headers: Mapping[str, str],
    *,
    quota_key: str,
) -> str | None:
    header_value = _anthropic_session_header(headers)
    if header_value:
        return f"claude:{quota_key}:session:{_hash_for_key(header_value)}"
    derived = _derive_anthropic_session_material(payload)
    if not derived:
        return None
    return f"claude:{quota_key}:derived:{_hash_for_key(derived)}"


def _sticky_prefix(provider_name: str) -> str:
    return "glm" if provider_name == GLM_PROVIDER_NAME else "claude"


def _provider_label(provider_name: str) -> str:
    return "GLM" if provider_name == GLM_PROVIDER_NAME else "Anthropic"


def _no_available_accounts_code(provider_name: str) -> str:
    return "no_available_glm_accounts" if provider_name == GLM_PROVIDER_NAME else "no_available_anthropic_accounts"


def _quota_cooldown_code(provider_name: str) -> str:
    return "glm_quota_cooldown" if provider_name == GLM_PROVIDER_NAME else "anthropic_quota_cooldown"


def _other_provider_routing_message(provider_name: str) -> str:
    if provider_name == GLM_PROVIDER_NAME:
        return "OpenAI and Anthropic accounts are not eligible for GLM routing."
    return "OpenAI accounts are not eligible for Claude routing."


def _anthropic_session_header(headers: Mapping[str, str]) -> str | None:
    normalized = {key.lower(): value for key, value in headers.items()}
    for key in (
        "x-claude-session-id",
        "x-claude-conversation-id",
        "x-claude-code-session-id",
        "session_id",
        "x-codex-session-id",
        "x-codex-conversation-id",
        "x-codex-turn-state",
    ):
        value = normalized.get(key)
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if stripped:
            return stripped
    return None


def _derive_anthropic_session_material(payload: AnthropicMessageRequest) -> str | None:
    parts: list[str] = [payload.model.lower()]
    system_text = _anthropic_content_text(payload.system)
    if system_text:
        parts.append(system_text[:512])
    for message in payload.messages:
        if message.role == "user":
            user_text = _anthropic_content_text(message.content)
            if user_text:
                parts.append(user_text[:512])
            break
    if len(parts) == 1:
        return None
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


def _anthropic_content_text(content: object) -> str | None:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for item in content:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            parts.append(text)
            continue
        if isinstance(item, Mapping):
            value = item.get("text")
            if isinstance(value, str):
                parts.append(value)
    return "".join(parts) if parts else None


def _account_status_label(account: Account) -> str:
    status = account.status
    return status.value if hasattr(status, "value") else str(status)


def _anthropic_selection_quota_detail(
    *,
    quota_key: str,
    blocked_count: int,
    candidate_count: int | None,
    selection_error_code: str | None,
    selection_error_message: str | None,
    retry_at: int | None,
) -> str | None:
    parts: list[str] = []
    if blocked_count > 0:
        noun = "account" if blocked_count == 1 else "accounts"
        reset_text = f" until {datetime.fromtimestamp(retry_at).isoformat()}" if retry_at is not None else ""
        parts.append(f"{quota_key} cooldown excluded {blocked_count} {noun}{reset_text}")
    if candidate_count is not None and (blocked_count > 0 or selection_error_code):
        noun = "account" if candidate_count == 1 else "accounts"
        parts.append(f"{candidate_count} {noun} remained after the {quota_key} prefilter")
    if selection_error_code:
        reason = selection_error_code
        if selection_error_message and selection_error_message != "No available accounts":
            reason = f"{selection_error_message} ({selection_error_code})"
        parts.append(f"selector reason: {reason}")
    return "; ".join(parts) if parts else None


def _hash_for_key(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:32]


def _anthropic_cooldown_is_active(used_percent: float, reset_at: int | None, *, now: int) -> bool:
    if float(used_percent) < 100.0:
        return False
    return reset_at is None or int(reset_at) > now


def _build_anthropic_headers(
    inbound_headers: Mapping[str, str],
    access_token: str,
    *,
    provider_name: str = ANTHROPIC_PROVIDER_NAME,
    fast_mode: bool = False,
) -> dict[str, str]:
    settings = get_settings()
    headers = {
        key: value
        for key, value in filter_inbound_headers(inbound_headers).items()
        if key.lower() not in {"api-key", "x-api-key"}
    }
    lower_keys = {key.lower() for key in headers}
    headers["Authorization"] = f"Bearer {access_token}"
    if "anthropic-version" not in lower_keys:
        headers["anthropic-version"] = settings.anthropic_version
    if "content-type" not in lower_keys:
        headers["content-type"] = "application/json"
    if "accept" not in lower_keys:
        headers["accept"] = "text/event-stream"
    if provider_name == ANTHROPIC_PROVIDER_NAME:
        required_betas = [_ANTHROPIC_OAUTH_BETA]
        if fast_mode:
            required_betas.append(_ANTHROPIC_FAST_MODE_BETA)
        _merge_anthropic_beta_header(headers, required_betas)
    return headers


def _merge_anthropic_beta_header(headers: dict[str, str], required_betas: list[str]) -> None:
    beta_key = next((key for key in headers if key.lower() == "anthropic-beta"), "anthropic-beta")
    existing = headers.get(beta_key, "")
    merged: list[str] = []
    seen: set[str] = set()
    for raw_value in [*existing.split(","), *required_betas]:
        beta = raw_value.strip()
        if not beta:
            continue
        normalized = beta.lower()
        if normalized in seen:
            continue
        merged.append(beta)
        seen.add(normalized)
    if merged:
        headers[beta_key] = ", ".join(merged)


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


def _usage_from_json_body(raw: bytes) -> AnthropicUsage | None:
    if not raw:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    usage_data = payload.get("usage")
    if not isinstance(usage_data, dict):
        return None
    try:
        return AnthropicUsage.model_validate(usage_data)
    except ValidationError:
        return None


async def _read_error_message(resp: aiohttp.ClientResponse) -> str:
    return (await _read_error_details(resp)).message


async def _read_error_details(resp: aiohttp.ClientResponse) -> _AnthropicErrorDetails:
    raw = await resp.read()
    if not raw:
        return _AnthropicErrorDetails(f"Anthropic upstream returned HTTP {resp.status}")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _AnthropicErrorDetails(raw.decode("utf-8", errors="replace"))
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            error_type = error.get("type")
            if isinstance(message, str):
                return _AnthropicErrorDetails(
                    message,
                    error_type if isinstance(error_type, str) else None,
                )
        if isinstance(payload.get("message"), str):
            error_type = payload.get("type")
            return _AnthropicErrorDetails(
                payload["message"],
                error_type if isinstance(error_type, str) else None,
            )
    return _AnthropicErrorDetails(f"Anthropic upstream returned HTTP {resp.status}")


def _anthropic_error_type_for_status(status_code: int) -> str:
    if status_code == 429:
        return "rate_limit_error"
    if status_code == 529:
        return "overloaded_error"
    if status_code == 504:
        return "timeout_error"
    if status_code >= 500:
        return "api_error"
    if status_code in {401, 403}:
        return "authentication_error"
    if status_code == 404:
        return "not_found_error"
    return "invalid_request_error"


def _rate_limit_error_from_response(resp: aiohttp.ClientResponse, message: str) -> UpstreamError:
    error = UpstreamError(message=message)
    reset_at = _anthropic_rate_limit_reset_at(resp.headers)
    if reset_at is not None:
        error["resets_at"] = reset_at
    retry_after = _retry_after_seconds(resp.headers)
    if retry_after is not None:
        error["resets_in_seconds"] = retry_after
    return error


def _reset_at_from_rate_limit_error(error: UpstreamError) -> int:
    reset_at = error.get("resets_at")
    if isinstance(reset_at, int | float):
        return int(reset_at)
    reset_in_seconds = error.get("resets_in_seconds")
    if isinstance(reset_in_seconds, int | float):
        return int(time.time() + max(0, reset_in_seconds))
    return int(time.time() + _ANTHROPIC_DEFAULT_COOLDOWN_SECONDS)


def _anthropic_rate_limit_reset_at(headers: Mapping[str, str]) -> int | None:
    reset_candidates = [
        "anthropic-ratelimit-unified-reset",
        "anthropic-ratelimit-unified-overage-reset",
        "anthropic-ratelimit-requests-reset",
        "anthropic-ratelimit-tokens-reset",
        "anthropic-ratelimit-input-tokens-reset",
        "anthropic-ratelimit-output-tokens-reset",
        "anthropic-priority-input-tokens-reset",
        "anthropic-priority-output-tokens-reset",
        "anthropic-fast-input-tokens-reset",
        "anthropic-fast-output-tokens-reset",
    ]
    epochs = [_parse_reset_epoch(_get_header(headers, name)) for name in reset_candidates]
    future_epochs = [epoch for epoch in epochs if epoch is not None and epoch > int(time.time())]
    if future_epochs:
        return min(future_epochs)
    past_epochs = [epoch for epoch in epochs if epoch is not None]
    return max(past_epochs) if past_epochs else None


def _retry_after_seconds(headers: Mapping[str, str]) -> int | None:
    retry_after = _get_header(headers, "retry-after")
    if retry_after is None:
        return None
    try:
        return max(0, int(float(retry_after)))
    except ValueError:
        return None


def _parse_reset_epoch(value: str | None) -> int | None:
    """Parse a reset header as RFC 3339 or Unix epoch (seconds or milliseconds)."""
    if not value:
        return None
    normalized = value.strip()
    try:
        epoch = float(normalized)
    except ValueError:
        pass
    else:
        if epoch <= 0:
            return None
        if epoch > 1e12:  # milliseconds
            epoch /= 1000.0
        return int(epoch)
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        return int(datetime.fromisoformat(normalized).timestamp())
    except ValueError:
        return None


def _get_header(headers: Mapping[str, str], name: str) -> str | None:
    if hasattr(headers, "get"):
        value = headers.get(name)
        if value is not None:
            return value
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None
