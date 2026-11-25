from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from anyio import CancelScope

from app.core.auth.refresh import RefreshError
from app.core.balancer.exclusions import drop_excluded
from app.core.balancer.types import UpstreamError
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.exceptions import ProxyModelNotAllowed
from app.core.providers.gemini import GEMINI_PROVIDER_NAME
from app.core.providers.gemini_models import GEMINI_NEON_MODEL, GeminiModel
from app.db.models import Account, AccountStatus, StickySessionKind
from app.db.session import get_background_session
from app.modules.accounts.auth_manager import AccountNotOwnedError, AuthManager, is_locally_owned
from app.modules.accounts.gemini_project import GEMINI_TERMS_DISABLED_CODE
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service_data import ApiKeyData, ApiKeyRequestUsageBudget, ApiKeyUsageReservationData
from app.modules.proxy.gemini_affinity import gemini_sticky_key
from app.modules.proxy.gemini_code_assist import CodeAssistGeminiUpstream
from app.modules.proxy.gemini_errors import GeminiGatewayError
from app.modules.proxy.gemini_fake_upstream import FakeGeminiUpstream
from app.modules.proxy.gemini_request import (
    antigravity_auxiliary_model,
    apply_thinking_level,
    gemini_session_id,
    normalized_request,
)
from app.modules.proxy.gemini_stream import GeminiUsage
from app.modules.proxy.gemini_upstream import GeminiUpstream, GeminiUpstreamCall, GeminiUpstreamError
from app.modules.proxy.gemini_usage_log import GeminiLogEntry, record_gemini_request, settle_gemini_reservation
from app.modules.proxy.gemini_validation import validation_failure_code
from app.modules.proxy.load_balancer import LoadBalancer, selectable_accounts
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.proxy.request_admission import enforce_request_limits
from app.modules.proxy.request_policy import validate_model_access
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.quota_planner.repository import QuotaPlannerRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository

logger = logging.getLogger(__name__)

MAX_ACCOUNT_ATTEMPTS = 3
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


def configured_upstream() -> GeminiUpstream:
    if get_settings().gemini_upstream == "fake":
        return FakeGeminiUpstream()
    return CodeAssistGeminiUpstream()


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
    choice: GeminiModel
    reasoning_effort: str
    useragent: str | None
    reservation: ApiKeyUsageReservationData | None
    account: Account
    first_chunk: dict[str, Any] | None
    rest: AsyncGenerator[dict[str, Any], None]
    started_at: float
    first_token_at: float | None
    session_id: str | None = None
    finished: bool = False


class GeminiGateway:
    def __init__(self) -> None:
        self._load_balancer = LoadBalancer(_repo_factory)
        self._encryptor = TokenEncryptor()
        self._auxiliary_holds: dict[tuple[str, str], tuple[float, GeminiUpstreamError]] = {}
        self._auxiliary_last_used: dict[tuple[str, str], float] = {}

    async def require_pool(self, api_key: ApiKeyData | None) -> None:
        scoped = _scoped_account_ids(api_key)
        async with _repo_factory() as repos:
            servable = any(
                account.provider == GEMINI_PROVIDER_NAME
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
        choice: GeminiModel,
        body: dict[str, Any],
        raw_size: int,
        useragent: str | None,
    ) -> GeminiAttempt:
        sticky_key = gemini_sticky_key(choice.canonical_model, body)
        body = normalized_request(body)
        auxiliary = antigravity_auxiliary_model(body)
        access_model = choice.canonical_model
        if auxiliary == choice:
            try:
                validate_model_access(api_key, GEMINI_NEON_MODEL)
            except ProxyModelNotAllowed:
                pass
            else:
                access_model = GEMINI_NEON_MODEL
        validate_model_access(api_key, access_model)
        if auxiliary is not None and not choice.auxiliary:
            choice = auxiliary
        if choice.auxiliary:
            sticky_key = None
        effort = apply_thinking_level(body, choice)
        reservation = await enforce_request_limits(
            api_key,
            request_model=choice.canonical_model,
            request_service_tier=None,
            request_usage_budget=_usage_budget(body, raw_size),
            provider=GEMINI_PROVIDER_NAME,
            access_model=access_model,
        )
        try:
            return await self._attempt(api_key, choice, body, effort, useragent, reservation, sticky_key)
        except BaseException:
            with CancelScope(shield=True):
                await settle_gemini_reservation(reservation, model=choice.canonical_model, usage=None)
            raise

    async def _attempt(
        self,
        api_key: ApiKeyData | None,
        choice: GeminiModel,
        body: dict[str, Any],
        effort: str,
        useragent: str | None,
        reservation: ApiKeyUsageReservationData | None,
        sticky_key: str | None,
    ) -> GeminiAttempt:
        excluded: set[str] = set()
        refreshed: set[str] = set()
        last_error: GeminiGatewayError | None = None
        retry_account: Account | None = None
        selections = 0
        session_id = gemini_session_id(body)
        while retry_account is not None or selections < MAX_ACCOUNT_ATTEMPTS:
            if retry_account is None:
                selections += 1
            if choice.auxiliary and retry_account is None:
                retry_account = await self._select_auxiliary(api_key, choice.canonical_model, excluded, last_error)
            account = retry_account or await self._select(
                api_key, choice.canonical_model, excluded, last_error, sticky_key
            )
            retry_account = None
            upstream = configured_upstream()
            started_at = time.monotonic()
            try:
                access_token = await self._access_token(account, force=account.id in refreshed)
            except GeminiGatewayError as exc:
                last_error = exc
                excluded.add(account.id)
                continue
            call = GeminiUpstreamCall(
                model=choice.canonical_model,
                effort=effort,
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
            except asyncio.CancelledError:
                with CancelScope(shield=True):
                    await self._log_failure(api_key, account, choice, effort, useragent, None, started_at, session_id)
                raise
            except GeminiUpstreamError as exc:
                await self._log_failure(api_key, account, choice, effort, useragent, exc, started_at, session_id)
                last_error = GeminiGatewayError(
                    exc.status_code, exc.message, code=f"upstream_{exc.kind}", retry_at=exc.reset_at
                )
                if exc.kind == "auth" and account.id not in refreshed:
                    refreshed.add(account.id)
                    retry_account = account
                    continue
                if not await self._mark_failure(account, choice, exc):
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
                session_id=session_id,
            )
        raise last_error or GeminiGatewayError(
            503, "No Gemini account could serve this request.", code="no_gemini_account"
        )

    async def _select_auxiliary(
        self,
        api_key: ApiKeyData | None,
        model: str,
        excluded: set[str],
        last_error: GeminiGatewayError | None,
    ) -> Account:
        settings = get_settings()
        scoped = _scoped_account_ids(api_key)
        async with _repo_factory() as repos:
            accounts = selectable_accounts(
                [
                    account
                    for account in await repos.accounts.list_accounts()
                    if account.provider == GEMINI_PROVIDER_NAME
                    and (scoped is None or account.id in scoped)
                    and is_locally_owned(account, settings)
                ]
            )
        now = time.time()
        self._auxiliary_holds = {key: hold for key, hold in self._auxiliary_holds.items() if hold[0] > now}
        soonest: tuple[float, GeminiUpstreamError] | None = None
        for account in sorted(
            drop_excluded(accounts, accounts, model),
            key=lambda account: self._auxiliary_last_used.get((model, account.id), 0.0),
        ):
            if account.id in excluded:
                continue
            key = (model, account.id)
            if hold := self._auxiliary_holds.get(key):
                if soonest is None or hold[0] < soonest[0]:
                    soonest = hold
                continue
            self._auxiliary_last_used[key] = now
            return account
        if last_error is not None:
            raise last_error
        if soonest is not None:
            retry_at, error = soonest
            raise GeminiGatewayError(error.status_code, error.message, code=f"upstream_{error.kind}", retry_at=retry_at)
        raise GeminiGatewayError(503, "No Gemini account is available.", code="no_gemini_account")

    async def _select(
        self,
        api_key: ApiKeyData | None,
        model: str,
        excluded: set[str],
        last_error: GeminiGatewayError | None,
        sticky_key: str | None,
    ) -> Account:
        selection = await self._load_balancer.select_account(
            sticky_key=sticky_key,
            sticky_kind=StickySessionKind.CODEX_SESSION if sticky_key else None,
            model=model,
            provider=GEMINI_PROVIDER_NAME,
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

    async def _mark_failure(self, account: Account, choice: GeminiModel, exc: GeminiUpstreamError) -> bool:
        if choice.auxiliary and exc.kind in ("rate_limit", "quota", "transient"):
            key = (choice.canonical_model, account.id)
            delay = 5 if exc.kind == "transient" else 60
            until = exc.reset_at or time.time() + delay
            previous = self._auxiliary_holds.get(key)
            if previous is None or previous[0] < until:
                self._auxiliary_holds[key] = (until, exc)
            return True
        error: UpstreamError = {"message": exc.message}
        if exc.reset_at is not None:
            error["quota_reset_at"] = math.ceil(exc.reset_at)
        if exc.kind == "rate_limit":
            await self._load_balancer.mark_rate_limit(account, error)
            return True
        if exc.kind == "quota":
            await self._load_balancer.mark_quota_exceeded(account, error)
            return True
        if exc.kind == "terms_disabled":
            await self._load_balancer.mark_permanent_failure(account, GEMINI_TERMS_DISABLED_CODE)
            logger.error(
                "gemini_account_terms_disabled alert=true account_id=%s status=%s", account.id, exc.status_code
            )
            return True
        if exc.kind == "validation_required":
            await self._load_balancer.mark_permanent_failure(account, validation_failure_code(exc.validation_url))
            logger.error(
                "gemini_account_validation_required alert=true account_id=%s validation_url=%s",
                account.id,
                exc.validation_url,
            )
            return True
        await self._load_balancer.record_error(account)
        return exc.kind == "transient"

    async def _log_failure(
        self,
        api_key: ApiKeyData | None,
        account: Account,
        choice: GeminiModel,
        effort: str,
        useragent: str | None,
        exc: GeminiUpstreamError | None,
        started_at: float,
        session_id: str | None,
    ) -> None:
        await record_gemini_request(
            GeminiLogEntry(
                api_key=api_key,
                account=account,
                model=choice.canonical_model,
                reasoning_effort=effort,
                useragent=useragent,
                usage=None,
                latency_ms=int((time.monotonic() - started_at) * 1000),
                latency_first_token_ms=None,
                status="error",
                session_id=session_id,
                error_code=f"upstream_{exc.kind}" if exc else "client_cancelled",
                error_message=exc.message if exc else None,
                upstream_status_code=exc.status_code if exc else None,
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
                    model=attempt.choice.canonical_model,
                    reasoning_effort=attempt.reasoning_effort,
                    useragent=attempt.useragent,
                    usage=usage,
                    latency_ms=int((time.monotonic() - attempt.started_at) * 1000),
                    latency_first_token_ms=first_ms,
                    status=status,
                    session_id=attempt.session_id,
                    error_code=f"upstream_{error.kind}"
                    if error is not None
                    else ("client_cancelled" if cancelled else None),
                    error_message=error.message if error is not None else None,
                    upstream_status_code=error.status_code if error is not None else None,
                )
            )
            if error is not None:
                await self._mark_failure(attempt.account, attempt.choice, error)
            elif not cancelled:
                await self._load_balancer.record_success(attempt.account)
        finally:
            await settle_gemini_reservation(attempt.reservation, model=attempt.choice.canonical_model, usage=usage)
