from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from anyio import CancelScope

from app.core.auth.refresh import RefreshError
from app.core.balancer.types import UpstreamError
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus
from app.db.session import get_background_session
from app.modules.accounts.auth_manager import AccountNotOwnedError, AuthManager
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service_data import ApiKeyData, ApiKeyRequestUsageBudget, ApiKeyUsageReservationData
from app.modules.proxy.gemini_errors import GeminiGatewayError
from app.modules.proxy.gemini_fake_upstream import FakeGeminiUpstream
from app.modules.proxy.gemini_models import GEMINI_PROVIDER, GeminiModelChoice, apply_thinking_level
from app.modules.proxy.gemini_stream import GeminiUsage
from app.modules.proxy.gemini_upstream import GeminiUpstream, GeminiUpstreamCall, GeminiUpstreamError
from app.modules.proxy.gemini_usage_log import GeminiLogEntry, record_gemini_request, settle_gemini_reservation
from app.modules.proxy.load_balancer import LoadBalancer
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.proxy.request_admission import enforce_request_limits
from app.modules.proxy.request_policy import validate_model_access
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.quota_planner.repository import QuotaPlannerRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository

logger = logging.getLogger(__name__)

MAX_ACCOUNT_ATTEMPTS = 3
TERMS_DISABLED_CODE = "gemini_terms_disabled"
_UNSERVABLE_STATUSES = frozenset({AccountStatus.DEACTIVATED, AccountStatus.REAUTH_REQUIRED})


@asynccontextmanager
async def _repo_factory() -> AsyncIterator[ProxyRepositories]:
    async with get_background_session() as session:
        yield ProxyRepositories(
            accounts=AccountsRepository(session),
            usage=UsageRepository(session),
            request_logs=RequestLogsRepository(session),
            sticky_sessions=StickySessionsRepository(session),
            api_keys=ApiKeysRepository(session),
            additional_usage=AdditionalUsageRepository(session),
            quota_planner=QuotaPlannerRepository(session),
        )


@asynccontextmanager
async def _accounts_scope() -> AsyncIterator[AccountsRepository]:
    async with _repo_factory() as repos:
        yield repos.accounts


def _scoped_account_ids(api_key: ApiKeyData | None) -> set[str] | None:
    if api_key is None or not api_key.account_assignment_scope_enabled:
        return None
    return set(api_key.assigned_account_ids)


def configured_upstream() -> GeminiUpstream | None:
    if get_settings().gemini_upstream == "fake":
        return FakeGeminiUpstream()
    return None


def _usage_budget(body: dict[str, Any], raw_size: int) -> ApiKeyRequestUsageBudget:
    config = body.get("generationConfig")
    max_output = config.get("maxOutputTokens") if isinstance(config, dict) else None
    return ApiKeyRequestUsageBudget(
        input_tokens=max(1, raw_size // 4),
        output_tokens=int(max_output) if isinstance(max_output, int) and max_output > 0 else None,
    )


@dataclass(slots=True)
class GeminiAttempt:
    api_key: ApiKeyData | None
    choice: GeminiModelChoice
    reasoning_effort: str
    useragent: str | None
    reservation: ApiKeyUsageReservationData | None
    account: Account
    first_chunk: dict[str, Any] | None
    rest: AsyncGenerator[dict[str, Any], None]
    started_at: float
    first_token_at: float | None
    finished: bool = False


class GeminiGateway:
    def __init__(self) -> None:
        self._load_balancer = LoadBalancer(_repo_factory)
        self._encryptor = TokenEncryptor()

    async def require_pool(self, api_key: ApiKeyData | None) -> None:
        scoped = _scoped_account_ids(api_key)
        async with _repo_factory() as repos:
            servable = any(
                account.provider == GEMINI_PROVIDER
                and account.status not in _UNSERVABLE_STATUSES
                and (scoped is None or account.id in scoped)
                for account in await repos.accounts.list_accounts()
            )
        if not servable:
            raise GeminiGatewayError(503, "No Gemini account is connected to agent-lb.", code="no_gemini_account")

    async def open(
        self,
        *,
        api_key: ApiKeyData | None,
        choice: GeminiModelChoice,
        body: dict[str, Any],
        raw_size: int,
        useragent: str | None,
    ) -> GeminiAttempt:
        validate_model_access(api_key, choice.canonical)
        effort = apply_thinking_level(body, choice)
        reservation = await enforce_request_limits(
            api_key,
            request_model=choice.canonical,
            request_service_tier=None,
            request_usage_budget=_usage_budget(body, raw_size),
        )
        try:
            return await self._attempt(api_key, choice, body, effort, useragent, reservation)
        except BaseException:
            with CancelScope(shield=True):
                await settle_gemini_reservation(reservation, model=choice.canonical, usage=None)
            raise

    async def _attempt(
        self,
        api_key: ApiKeyData | None,
        choice: GeminiModelChoice,
        body: dict[str, Any],
        effort: str,
        useragent: str | None,
        reservation: ApiKeyUsageReservationData | None,
    ) -> GeminiAttempt:
        excluded: set[str] = set()
        refreshed: set[str] = set()
        last_error: GeminiGatewayError | None = None
        retry_account: Account | None = None
        selections = 0
        while retry_account is not None or selections < MAX_ACCOUNT_ATTEMPTS:
            if retry_account is None:
                selections += 1
            account = retry_account or await self._select(api_key, choice.canonical, excluded, last_error)
            retry_account = None
            upstream = configured_upstream()
            if upstream is None:
                raise GeminiGatewayError(
                    501, "The Gemini upstream adapter is not configured on this agent-lb.", code="gemini_upstream_off"
                )
            started_at = time.monotonic()
            access_token = await self._access_token(account, force=account.id in refreshed)
            call = GeminiUpstreamCall(
                model=choice.canonical,
                body=body,
                access_token=access_token,
                project_id=account.workspace_id,
                account_id=account.id,
            )
            stream = upstream.stream(call)
            try:
                first: dict[str, Any] | None = await anext(stream)
            except StopAsyncIteration:
                first = None
            except GeminiUpstreamError as exc:
                await self._log_failure(api_key, account, choice, effort, useragent, exc, started_at)
                last_error = GeminiGatewayError(
                    exc.status_code, exc.message, code=f"upstream_{exc.kind}", retry_at=exc.reset_at
                )
                if exc.kind == "auth" and account.id not in refreshed:
                    refreshed.add(account.id)
                    retry_account = account
                    continue
                if not await self._mark_failure(account, exc):
                    raise last_error from exc
                excluded.add(account.id)
                continue
            return GeminiAttempt(
                api_key=api_key,
                choice=choice,
                reasoning_effort=effort,
                useragent=useragent,
                reservation=reservation,
                account=account,
                first_chunk=first,
                rest=stream,
                started_at=started_at,
                first_token_at=time.monotonic() if first is not None else None,
            )
        raise last_error or GeminiGatewayError(
            503, "No Gemini account could serve this request.", code="no_gemini_account"
        )

    async def _select(
        self,
        api_key: ApiKeyData | None,
        model: str,
        excluded: set[str],
        last_error: GeminiGatewayError | None,
    ) -> Account:
        selection = await self._load_balancer.select_account(
            model=model,
            provider=GEMINI_PROVIDER,
            account_ids=_scoped_account_ids(api_key),
            exclude_account_ids=excluded,
            routing_strategy="usage_weighted",
            prefer_earlier_reset_accounts=True,
        )
        if selection.account is not None:
            return selection.account
        if last_error is not None:
            raise last_error
        retry_at = selection.retry_at.timestamp() if selection.retry_at is not None else None
        message = selection.error_message or "No Gemini account is available."
        if message.startswith("No available accounts") or selection.error_code == "no_accounts":
            message = "No Gemini account is connected to agent-lb."
        raise GeminiGatewayError(503, message, code=selection.error_code or "no_gemini_account", retry_at=retry_at)

    async def _access_token(self, account: Account, *, force: bool) -> str:
        try:
            async with _repo_factory() as repos:
                latest = await repos.accounts.get_by_id(account.id)
                if latest is None:
                    raise GeminiGatewayError(503, "The selected Gemini account no longer exists.", code="account_gone")
                manager = AuthManager(repos.accounts, refresh_repo_factory=_accounts_scope)
                fresh = await manager.ensure_fresh(latest, force=force)
                return self._encryptor.decrypt(fresh.access_token_encrypted)
        except AccountNotOwnedError as exc:
            raise GeminiGatewayError(
                503, "Gemini accounts are served only by their own agent-lb.", code="account_not_owned"
            ) from exc
        except RefreshError as exc:
            raise GeminiGatewayError(503, "The Gemini account sign-in needs renewal.", code=exc.code) from exc

    async def _mark_failure(self, account: Account, exc: GeminiUpstreamError) -> bool:
        error: UpstreamError = {"message": exc.message}
        if exc.reset_at is not None:
            error["quota_reset_at"] = int(exc.reset_at)
        if exc.kind == "rate_limit":
            await self._load_balancer.mark_rate_limit(account, error)
            return True
        if exc.kind == "quota":
            await self._load_balancer.mark_quota_exceeded(account, error)
            return True
        if exc.kind == "terms_disabled":
            await self._load_balancer.mark_permanent_failure(account, TERMS_DISABLED_CODE)
            logger.error(
                "gemini_account_terms_disabled alert=true account_id=%s status=%s", account.id, exc.status_code
            )
            return True
        await self._load_balancer.record_error(account)
        return exc.kind == "transient"

    async def _log_failure(
        self,
        api_key: ApiKeyData | None,
        account: Account,
        choice: GeminiModelChoice,
        effort: str,
        useragent: str | None,
        exc: GeminiUpstreamError,
        started_at: float,
    ) -> None:
        await record_gemini_request(
            GeminiLogEntry(
                api_key=api_key,
                account=account,
                model=choice.canonical,
                reasoning_effort=effort,
                useragent=useragent,
                usage=None,
                latency_ms=int((time.monotonic() - started_at) * 1000),
                latency_first_token_ms=None,
                status="error",
                error_code=f"upstream_{exc.kind}",
                error_message=exc.message,
                upstream_status_code=exc.status_code,
            )
        )

    async def finish(
        self,
        attempt: GeminiAttempt,
        usage: GeminiUsage | None,
        *,
        error: GeminiUpstreamError | None = None,
        cancelled: bool = False,
    ) -> None:
        if attempt.finished:
            return
        attempt.finished = True
        first_ms = (
            int((attempt.first_token_at - attempt.started_at) * 1000) if attempt.first_token_at is not None else None
        )
        status = "error" if error is not None or cancelled else "success"
        try:
            await record_gemini_request(
                GeminiLogEntry(
                    api_key=attempt.api_key,
                    account=attempt.account,
                    model=attempt.choice.canonical,
                    reasoning_effort=attempt.reasoning_effort,
                    useragent=attempt.useragent,
                    usage=usage,
                    latency_ms=int((time.monotonic() - attempt.started_at) * 1000),
                    latency_first_token_ms=first_ms,
                    status=status,
                    error_code=f"upstream_{error.kind}"
                    if error is not None
                    else ("client_cancelled" if cancelled else None),
                    error_message=error.message if error is not None else None,
                    upstream_status_code=error.status_code if error is not None else None,
                )
            )
            if error is not None:
                await self._mark_failure(attempt.account, error)
            elif not cancelled:
                await self._load_balancer.record_success(attempt.account)
        finally:
            await settle_gemini_reservation(attempt.reservation, model=attempt.choice.canonical, usage=usage)
