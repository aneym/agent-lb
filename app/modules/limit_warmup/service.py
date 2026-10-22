from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime
from typing import AsyncContextManager, Awaitable, Callable

from app.core.auth.refresh import RefreshError
from app.core.clients.proxy import UpstreamProxyRouteTrace, override_stream_timeouts, stream_responses
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.openai.models import OpenAIError, ResponseUsage
from app.core.openai.parsing import parse_sse_event
from app.core.openai.requests import ResponsesRequest
from app.core.providers import ANTHROPIC_PROVIDER_NAME, OPENAI_PROVIDER_NAME
from app.core.upstream_proxy import ResolvedUpstreamRoute, UpstreamProxyRouteError, resolve_upstream_route
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, AdditionalUsageHistory, DashboardSettings, UsageHistory
from app.db.request_models import AccountLimitWarmup
from app.modules.accounts.auth_manager import AuthManager, is_locally_owned
from app.modules.accounts.repository import AccountsRepository
from app.modules.limit_warmup.anthropic_primer import send_anthropic_primer
from app.modules.limit_warmup.constants import _ANTHROPIC_CONTINUOUS_WINDOW, _DEFAULT_WARMUP_INSTRUCTIONS, _MAX_CONCURRENT_WARMUP_SENDS, _OPENAI_CONTINUOUS_WINDOW, _SEED_WINDOW, _TERMINAL_ERROR_EVENTS, LIMIT_WARMUP_HEADER  # noqa: I001  # fmt: skip
from app.modules.limit_warmup.contracts import LimitWarmupAttemptsRepository, LimitWarmupRequestLogRepository, LimitWarmupSender, LimitWarmupSendOutcome, LimitWarmupSendResult  # noqa: I001  # fmt: skip
from app.modules.limit_warmup.policy import _account_is_safe_candidate, _anthropic_prime_key, _as_utc, _build_candidate, _in_cooldown, _openai_prime_key, _opened_by_prime, _primary_window_is_cold, _primary_window_minutes, _seed_reset_at, _selected_windows, _within_hold_window, _within_seed_window  # noqa: I001  # fmt: skip
from app.modules.quota_planner.logic import PlannerSettings

logger = logging.getLogger("app.modules.limit_warmup.service")

from app.modules.limit_warmup.completion import WarmupCompletion
from app.modules.limit_warmup.dispatch import WarmupDispatch


class StreamingLimitWarmupSender:
    def __init__(
        self,
        accounts_repo: AccountsRepository,
        *,
        accounts_repo_factory: Callable[[], AsyncContextManager[AccountsRepository]] | None = None,
    ) -> None:
        self._accounts_repo = accounts_repo
        self._accounts_repo_factory = accounts_repo_factory
        self._auth_manager = AuthManager(accounts_repo)
        self._encryptor = TokenEncryptor()
        self._auth_lock = asyncio.Lock()

    async def send(self, account: Account, *, model: str, prompt: str) -> LimitWarmupSendResult:
        if account.provider == ANTHROPIC_PROVIDER_NAME:
            return await self._send_anthropic(account, model=model, prompt=prompt)
        return await self._send_openai(account, model=model, prompt=prompt)

    async def _send_anthropic(self, account: Account, *, model: str, prompt: str) -> LimitWarmupSendResult:
        request_id = f"limit-warmup-{uuid.uuid4().hex}"
        started = time.monotonic()
        try:
            async with self._auth_lock:
                fresh_account = await self._ensure_fresh(account)
                access_token = self._encryptor.decrypt(fresh_account.access_token_encrypted)
        except RefreshError as exc:
            return LimitWarmupSendResult(
                request_id=request_id,
                success=False,
                latency_ms=_elapsed_ms(started),
                error_code=f"auth_refresh_{exc.code}",
                error_message=exc.message,
            )

        if fresh_account.status != AccountStatus.ACTIVE:
            return LimitWarmupSendResult(
                request_id=request_id,
                success=False,
                latency_ms=_elapsed_ms(started),
                error_code="account_not_active",
                error_message=f"Account status is {fresh_account.status.value}",
            )

        primer = await send_anthropic_primer(
            access_token,
            model=model,
            prompt=prompt,
            request_id=request_id,
            warmup_header=LIMIT_WARMUP_HEADER,
        )
        return LimitWarmupSendResult(
            request_id=request_id,
            success=primer.success,
            latency_ms=_elapsed_ms(started),
            input_tokens=primer.input_tokens,
            output_tokens=primer.output_tokens,
            cached_input_tokens=primer.cached_input_tokens,
            error_code=primer.error_code,
            error_message=primer.error_message,
        )

    async def _send_openai(self, account: Account, *, model: str, prompt: str) -> LimitWarmupSendResult:
        request_id = f"limit-warmup-{uuid.uuid4().hex}"
        started = time.monotonic()
        try:
            async with self._auth_lock:
                fresh_account = await self._ensure_fresh(account)
                access_token = self._encryptor.decrypt(fresh_account.access_token_encrypted)
                chatgpt_account_id = fresh_account.chatgpt_account_id
        except RefreshError as exc:
            return LimitWarmupSendResult(
                request_id=request_id,
                success=False,
                latency_ms=_elapsed_ms(started),
                error_code=f"auth_refresh_{exc.code}",
                error_message=exc.message,
            )

        if fresh_account.status != AccountStatus.ACTIVE:
            return LimitWarmupSendResult(
                request_id=request_id,
                success=False,
                latency_ms=_elapsed_ms(started),
                error_code="account_not_active",
                error_message=f"Account status is {fresh_account.status.value}",
            )
        try:
            route = await self._resolve_upstream_route(fresh_account)
        except UpstreamProxyRouteError as exc:
            return LimitWarmupSendResult(
                request_id=request_id,
                success=False,
                latency_ms=_elapsed_ms(started),
                error_code="upstream_proxy_unavailable",
                error_message=f"Upstream proxy route unavailable: {exc.reason}",
                upstream_proxy_fail_closed_reason=exc.reason,
            )

        payload = ResponsesRequest.model_validate(
            {
                "model": model,
                "instructions": _DEFAULT_WARMUP_INSTRUCTIONS,
                "input": prompt,
                "tools": [],
                "parallel_tool_calls": False,
                "stream": True,
                "store": False,
                "max_output_tokens": 4,
            }
        )
        headers = {
            "x-request-id": request_id,
            LIMIT_WARMUP_HEADER: "1",
            "user-agent": "agent-lb-limit-warmup",
        }
        usage: ResponseUsage | None = None
        route_trace = UpstreamProxyRouteTrace()
        with override_stream_timeouts(
            connect_timeout_seconds=5.0,
            idle_timeout_seconds=10.0,
            total_timeout_seconds=30.0,
        ):
            async for event_block in stream_responses(
                payload,
                headers,
                access_token,
                chatgpt_account_id,
                upstream_stream_transport_override="http",
                route=route,
                route_trace=route_trace,
                allow_direct_egress=route is None,
            ):
                event = parse_sse_event(event_block)
                if event is None:
                    continue
                if event.response is not None and event.response.usage is not None:
                    usage = event.response.usage
                if event.type == "response.completed":
                    return LimitWarmupSendResult(
                        request_id=request_id,
                        success=True,
                        latency_ms=_elapsed_ms(started),
                        usage=usage,
                        upstream_proxy_route_mode=route_trace.mode,
                        upstream_proxy_pool_id=route_trace.pool_id,
                        upstream_proxy_endpoint_id=route_trace.endpoint_id,
                        upstream_proxy_fallback_used=route_trace.fallback_used,
                    )
                if event.type in _TERMINAL_ERROR_EVENTS:
                    error = _event_error(event.error, event.response.error if event.response is not None else None)
                    return LimitWarmupSendResult(
                        request_id=request_id,
                        success=False,
                        latency_ms=_elapsed_ms(started),
                        usage=usage,
                        error_code=error.code or event.type,
                        error_message=error.message or event.type,
                        upstream_proxy_route_mode=route_trace.mode,
                        upstream_proxy_pool_id=route_trace.pool_id,
                        upstream_proxy_endpoint_id=route_trace.endpoint_id,
                        upstream_proxy_fallback_used=route_trace.fallback_used,
                    )

        return LimitWarmupSendResult(
            request_id=request_id,
            success=False,
            latency_ms=_elapsed_ms(started),
            usage=usage,
            error_code="stream_incomplete",
            error_message="Warm-up stream ended without a terminal event",
            upstream_proxy_route_mode=route_trace.mode,
            upstream_proxy_pool_id=route_trace.pool_id,
            upstream_proxy_endpoint_id=route_trace.endpoint_id,
            upstream_proxy_fallback_used=route_trace.fallback_used,
        )

    async def _ensure_fresh(self, account: Account) -> Account:
        if self._accounts_repo_factory is None:
            return await self._auth_manager.ensure_fresh(account)
        async with self._accounts_repo_factory() as accounts_repo:
            return await AuthManager(
                accounts_repo,
                refresh_repo_factory=self._accounts_repo_factory,
            ).ensure_fresh(account)

    async def _resolve_upstream_route(self, account: Account) -> ResolvedUpstreamRoute | None:
        if self._accounts_repo_factory is not None:
            async with self._accounts_repo_factory() as accounts_repo:
                return await resolve_upstream_route(
                    accounts_repo.session,
                    account_id=account.id,
                    operation="limit_warmup",
                    scope="account",
                    encryptor=self._encryptor,
                )
        return await resolve_upstream_route(
            self._accounts_repo.session,
            account_id=account.id,
            operation="limit_warmup",
            scope="account",
            encryptor=self._encryptor,
        )


class LimitWarmupService:
    def __init__(
        self,
        warmup_repo: LimitWarmupAttemptsRepository,
        request_logs_repo: LimitWarmupRequestLogRepository,
        *,
        sender: LimitWarmupSender | None = None,
        confirm_anthropic_prime: Callable[[str, int], Awaitable[bool]] | None = None,
    ) -> None:
        self._warmup_repo = warmup_repo
        self._sender = sender
        self._dispatch = WarmupDispatch(warmup_repo)
        self._completion = WarmupCompletion(
            warmup_repo, request_logs_repo, confirm_anthropic_prime=confirm_anthropic_prime
        )

    async def run_after_usage_refresh(
        self,
        *,
        accounts: list[Account],
        settings: DashboardSettings,
        before_primary: dict[str, UsageHistory],
        before_secondary: dict[str, UsageHistory],
        after_primary: dict[str, UsageHistory],
        after_secondary: dict[str, UsageHistory],
        anthropic_standard: dict[str, AdditionalUsageHistory] | None = None,
        planner_settings: PlannerSettings | None = None,
        now: datetime | None = None,
    ) -> None:
        selected_windows = _selected_windows(settings.limit_warmup_windows)

        if (
            not selected_windows
            and planner_settings is None
            and not any(account.provider in {ANTHROPIC_PROVIDER_NAME, OPENAI_PROVIDER_NAME} for account in accounts)
        ):
            return
        current = now or utcnow()

        account_ids = [account.id for account in accounts]
        latest_attempts = await self._warmup_repo.latest_by_account(account_ids)
        sender = self._sender
        if sender is None:
            raise RuntimeError("LimitWarmupService requires a sender")
        send_tasks: dict[asyncio.Task[LimitWarmupSendOutcome], AccountLimitWarmup] = {}
        send_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_WARMUP_SENDS)

        for account in accounts:
            if not _account_is_safe_candidate(account):
                continue
            if not account.limit_warmup_enabled:
                continue
            latest_attempt = latest_attempts.get(account.id)
            if account.provider == ANTHROPIC_PROVIDER_NAME:
                observed = after_primary.get(account.id)
                if (
                    latest_attempt is not None
                    and latest_attempt.window == _ANTHROPIC_CONTINUOUS_WINDOW
                    and latest_attempt.status == "sent_unconfirmed"
                    and observed is not None
                    and observed.reset_at is not None
                    and _opened_by_prime(latest_attempt, observed.reset_at)
                    and _as_utc(observed.recorded_at) > _as_utc(latest_attempt.attempted_at)
                ):
                    await self._warmup_repo.complete_attempt(
                        latest_attempt.id, status="succeeded", completed_at=utcnow()
                    )
                    logger.info(
                        "Anthropic primer result account_id=%s reset_at=%s status=succeeded confirmation=delayed",
                        account.id,
                        latest_attempt.reset_at,
                    )
                    continue
                prime_key = _anthropic_prime_key(
                    after_primary.get(account.id),
                    after_secondary.get(account.id),
                    anthropic_standard.get(account.id) if anthropic_standard else None,
                    latest_attempt,
                    current,
                )
                if prime_key is not None:
                    enqueued = await self._dispatch._enqueue_warmup_send(
                        account=account,
                        window=_ANTHROPIC_CONTINUOUS_WINDOW,
                        reset_at=prime_key,
                        settings=settings,
                        sender=sender,
                        semaphore=send_semaphore,
                        latest_attempts=latest_attempts,
                        now=current,
                    )
                    if enqueued is not None:
                        send_task, attempt = enqueued
                        send_tasks[send_task] = attempt
                continue
            if not settings.limit_warmup_enabled:
                continue
            if account.provider == OPENAI_PROVIDER_NAME and is_locally_owned(account, get_settings()):
                prime_key = _openai_prime_key(
                    after_primary.get(account.id), after_secondary.get(account.id), latest_attempt, current
                )
                if prime_key is not None:
                    enqueued = await self._dispatch._enqueue_warmup_send(
                        account=account,
                        window=_OPENAI_CONTINUOUS_WINDOW,
                        reset_at=prime_key,
                        settings=settings,
                        sender=sender,
                        semaphore=send_semaphore,
                        latest_attempts=latest_attempts,
                        now=current,
                    )
                    if enqueued is not None:
                        send_task, attempt = enqueued
                        send_tasks[send_task] = attempt
                    continue
            if _in_cooldown(
                latest_attempt,
                cooldown_seconds=settings.limit_warmup_cooldown_seconds,
            ):
                continue

            window_minutes = _primary_window_minutes(account, after_primary.get(account.id))

            if planner_settings is not None and _within_seed_window(current, planner_settings, window_minutes):
                if _primary_window_is_cold(account, after_primary.get(account.id), current):
                    enqueued = await self._dispatch._enqueue_warmup_send(
                        account=account,
                        window=_SEED_WINDOW,
                        reset_at=_seed_reset_at(current, planner_settings),
                        settings=settings,
                        sender=sender,
                        semaphore=send_semaphore,
                        latest_attempts=latest_attempts,
                    )
                    if enqueued is not None:
                        send_task, attempt = enqueued
                        send_tasks[send_task] = attempt
                    continue

            if planner_settings is not None and not _within_hold_window(current, planner_settings, window_minutes):
                continue

            for window in selected_windows:
                candidate = _build_candidate(
                    account=account,
                    window=window,
                    before_primary=before_primary,
                    before_secondary=before_secondary,
                    after_primary=after_primary,
                    after_secondary=after_secondary,
                    min_available_percent=settings.limit_warmup_min_available_percent,
                )
                if candidate is None:
                    continue

                enqueued = await self._dispatch._enqueue_warmup_send(
                    account=account,
                    window=window,
                    reset_at=candidate.reset_at,
                    settings=settings,
                    sender=sender,
                    semaphore=send_semaphore,
                    latest_attempts=latest_attempts,
                )
                if enqueued is None:
                    continue
                send_task, attempt = enqueued
                send_tasks[send_task] = attempt

        await self._completion.drain(send_tasks, latest_attempts)


def _event_error(*errors: OpenAIError | None) -> OpenAIError:
    for error in errors:
        if error is not None:
            return error
    return OpenAIError(message=None, code=None)


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))
