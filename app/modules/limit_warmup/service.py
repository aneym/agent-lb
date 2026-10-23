from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncContextManager, Awaitable, Callable, Protocol

from app.core import usage as usage_core
from app.core.auth.refresh import RefreshError
from app.core.clients.proxy import UpstreamProxyRouteTrace, override_stream_timeouts, stream_responses
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.openai.models import OpenAIError, ResponseUsage
from app.core.openai.parsing import parse_sse_event
from app.core.openai.requests import ResponsesRequest
from app.core.plan_types import normalize_account_plan_type
from app.core.providers import ANTHROPIC_PROVIDER_NAME, OPENAI_PROVIDER_NAME
from app.core.upstream_proxy import ResolvedUpstreamRoute, UpstreamProxyRouteError, resolve_upstream_route
from app.core.utils.time import naive_utc_to_epoch, utcnow
from app.db.models import (
    Account,
    AccountLimitWarmup,
    AccountStatus,
    AdditionalUsageHistory,
    DashboardSettings,
    UsageHistory,
)
from app.modules.accounts.auth_manager import AuthManager, is_locally_owned
from app.modules.accounts.repository import AccountsRepository
from app.modules.limit_warmup.anthropic_primer import send_anthropic_primer
from app.modules.limit_warmup.completion import WarmupCompletion
from app.modules.limit_warmup.constants import (
    _ANTHROPIC_CONTINUOUS_WINDOW,
    _DEFAULT_WARMUP_INSTRUCTIONS,
    _MAX_CONCURRENT_WARMUP_SENDS,
    _OPENAI_CONTINUOUS_WINDOW,
    _SEED_WINDOW,
    _TERMINAL_ERROR_EVENTS,
    LIMIT_WARMUP_HEADER,
)
from app.modules.limit_warmup.contracts import LimitWarmupSender, LimitWarmupSendOutcome, LimitWarmupSendResult
from app.modules.limit_warmup.dispatch import WarmupDispatch
from app.modules.limit_warmup.policy import (
    _account_is_safe_candidate,
    _anthropic_prime_key,
    _as_utc,
    _in_cooldown,
    _openai_prime_key,
    _opened_by_prime,
    _primary_window_is_cold,
    _primary_window_minutes,
    _seed_reset_at,
    _selected_windows,
    _within_hold_window,
    _within_seed_window,
)
from app.modules.quota_planner.logic import PlannerSettings
from app.modules.usage.mappers import usage_history_to_window_row

logger = logging.getLogger("app.modules.limit_warmup.service")


_ROLLING_WINDOW_SECONDS = 300 * 60


_SHORT_WINDOW_MAX_MINUTES = 24 * 60


_STAGGER_SLOT_GRACE_SECONDS = 60


_IDLE_PRIMARY_WINDOW = "primary_idle"


_RESET_CONFIRMED_MIN_JUMP_SECONDS = 60


_RESET_AT_JITTER_TOLERANCE_SECONDS = 5


class LimitWarmupAttemptsRepository(Protocol):
    async def latest_by_account(self, account_ids: list[str]) -> dict[str, AccountLimitWarmup]: ...

    async def try_create_attempt(
        self,
        *,
        account_id: str,
        window: str,
        reset_at: int,
        model: str,
        attempted_at,
        status: str = "pending",
        reset_at_tolerance_seconds: int = 0,
    ) -> AccountLimitWarmup | None: ...

    async def complete_attempt(
        self,
        attempt_id: int,
        *,
        status: str,
        completed_at,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AccountLimitWarmup | None: ...


class LimitWarmupRequestLogRepository(Protocol):
    async def add_log(
        self,
        account_id: str | None,
        request_id: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        latency_ms: int | None,
        status: str,
        error_code: str | None,
        latency_first_token_ms: int | None = None,
        error_message: str | None = None,
        requested_at: datetime | None = None,
        cached_input_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        reasoning_effort: str | None = None,
        service_tier: str | None = None,
        requested_service_tier: str | None = None,
        actual_service_tier: str | None = None,
        transport: str | None = None,
        upstream_transport: str | None = None,
        api_key_id: str | None = None,
        session_id: str | None = None,
        plan_type: str | None = None,
        source: str | None = None,
        useragent: str | None = None,
        useragent_group: str | None = None,
        client_ip: str | None = None,
        failure_phase: str | None = None,
        failure_detail: str | None = None,
        failure_exception_type: str | None = None,
        upstream_status_code: int | None = None,
        upstream_error_code: str | None = None,
        bridge_stage: str | None = None,
        request_kind: str = "normal",
        upstream_proxy_route_mode: str | None = None,
        upstream_proxy_pool_id: str | None = None,
        upstream_proxy_endpoint_id: str | None = None,
        upstream_proxy_fallback_used: bool | None = None,
        upstream_proxy_fail_closed_reason: str | None = None,
    ) -> object: ...


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
                if (
                    fresh_account is None
                    or not _account_is_safe_candidate(fresh_account)
                    or not fresh_account.limit_warmup_enabled
                ):
                    if fresh_account is None:
                        error_message = "Account no longer exists"
                    elif not fresh_account.limit_warmup_enabled:
                        error_message = "Limit warm-up is disabled for this account"
                    else:
                        error_message = f"Account status is {fresh_account.status.value}"
                    return LimitWarmupSendResult(
                        request_id=request_id,
                        success=False,
                        latency_ms=_elapsed_ms(started),
                        error_code="account_not_active",
                        error_message=error_message,
                    )
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
            "user-agent": "codex-lb-limit-warmup",
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
                codex_lb_account_id=fresh_account.id,
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

    async def _ensure_fresh(self, account: Account) -> Account | None:
        if self._accounts_repo_factory is None:
            current = await self._accounts_repo.get_by_id_fresh(account.id)
            if current is None or not _account_is_safe_candidate(current) or not current.limit_warmup_enabled:
                return current
            await self._auth_manager.ensure_fresh(current)
            return await self._accounts_repo.get_by_id_fresh(account.id)
        async with self._accounts_repo_factory() as accounts_repo:
            current = await accounts_repo.get_by_id_fresh(account.id)
            if current is None or not _account_is_safe_candidate(current) or not current.limit_warmup_enabled:
                return current
            await AuthManager(
                accounts_repo,
                refresh_repo_factory=self._accounts_repo_factory,
            ).ensure_fresh(current)
        async with self._accounts_repo_factory() as accounts_repo:
            return await accounts_repo.get_by_id_fresh(account.id)

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
        stagger_accounts: list[Account] | None = None,
        settings: DashboardSettings,
        before_primary: dict[str, UsageHistory],
        before_secondary: dict[str, UsageHistory],
        after_primary: dict[str, UsageHistory],
        after_secondary: dict[str, UsageHistory],
        anthropic_standard: dict[str, AdditionalUsageHistory] | None = None,
        planner_settings: PlannerSettings | None = None,
        now: datetime | None = None,
        previous_plan_types: dict[str, str | None] | None = None,
        refresh_started_at: datetime | None = None,
        usage_refresh_interval_seconds: int = _STAGGER_SLOT_GRACE_SECONDS,
    ) -> None:
        selected_windows = _selected_windows(settings.limit_warmup_windows)
        if (
            not selected_windows
            and planner_settings is None
            and not any(a.provider == ANTHROPIC_PROVIDER_NAME for a in accounts)
        ):
            return

        account_ids = [account.id for account in accounts]
        latest_attempts = await self._warmup_repo.latest_by_account(account_ids)
        sender = self._sender
        if sender is None:
            raise RuntimeError("LimitWarmupService requires a sender")
        send_tasks: dict[asyncio.Task[LimitWarmupSendOutcome], AccountLimitWarmup] = {}
        send_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_WARMUP_SENDS)
        stagger_source = accounts if stagger_accounts is None else stagger_accounts
        staggered_accounts = [
            account
            for account in stagger_source
            if _account_is_safe_candidate(account) and account.limit_warmup_enabled
        ]
        current = now or utcnow()

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
            secondary_reset_pending = "secondary" in selected_windows and (
                _build_candidate(
                    account=account,
                    window="secondary",
                    before_primary=before_primary,
                    before_secondary=before_secondary,
                    after_primary=after_primary,
                    after_secondary=after_secondary,
                    min_available_percent=settings.limit_warmup_min_available_percent,
                )
                or _build_paid_to_free_transition_candidate(
                    account=account,
                    previous_plan_type=(previous_plan_types or {}).get(account.id),
                    after_secondary=after_secondary,
                    refresh_started_at=refresh_started_at,
                    min_available_percent=settings.limit_warmup_min_available_percent,
                )
            )
            if (
                account.provider == OPENAI_PROVIDER_NAME
                and is_locally_owned(account, get_settings())
                and not secondary_reset_pending
            ):
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

            windows_to_evaluate = list(selected_windows)
            if settings.limit_warmup_staggered_idle_enabled and "primary" not in windows_to_evaluate:
                windows_to_evaluate.append("primary")
            for window in windows_to_evaluate:
                candidate = None
                if window in selected_windows:
                    candidate = _build_candidate(
                        account=account,
                        window=window,
                        before_primary=before_primary,
                        before_secondary=before_secondary,
                        after_primary=after_primary,
                        after_secondary=after_secondary,
                        min_available_percent=settings.limit_warmup_min_available_percent,
                    )
                if candidate is None and window == "secondary":
                    candidate = _build_paid_to_free_transition_candidate(
                        account=account,
                        previous_plan_type=(previous_plan_types or {}).get(account.id),
                        after_secondary=after_secondary,
                        refresh_started_at=refresh_started_at,
                        min_available_percent=settings.limit_warmup_min_available_percent,
                    )
                if (
                    candidate is None
                    and _account_is_safe_candidate(account)
                    and settings.limit_warmup_staggered_idle_enabled
                    and window == "primary"
                ):
                    candidate = _build_staggered_idle_candidate(
                        account=account,
                        accounts=staggered_accounts,
                        now=current,
                        after_primary=after_primary,
                        refresh_started_at=refresh_started_at,
                        usage_refresh_interval_seconds=usage_refresh_interval_seconds,
                        idle_threshold_percent=settings.limit_warmup_idle_threshold_percent,
                    )
                if candidate is None:
                    continue
                if candidate.window == _IDLE_PRIMARY_WINDOW and _in_cooldown(
                    latest_attempt,
                    cooldown_seconds=settings.limit_warmup_cooldown_seconds,
                ):
                    continue

                model = self._dispatch._resolve_model(settings.limit_warmup_model, account)
                if model is None:
                    skipped = await self._warmup_repo.try_create_attempt(
                        account_id=account.id,
                        window=candidate.window,
                        reset_at=candidate.reset_at,
                        model="auto",
                        attempted_at=utcnow(),
                        reset_at_tolerance_seconds=_attempt_reset_at_tolerance(candidate),
                    )
                    if skipped is not None:
                        completed = await self._warmup_repo.complete_attempt(
                            skipped.id,
                            status="skipped",
                            completed_at=utcnow(),
                            error_code="model_unavailable",
                            error_message="No eligible priced text model was available for warm-up",
                        )
                        latest_attempts[account.id] = completed or skipped
                    continue

                attempt = await self._warmup_repo.try_create_attempt(
                    account_id=account.id,
                    window=candidate.window,
                    reset_at=candidate.reset_at,
                    model=model,
                    attempted_at=utcnow(),
                    reset_at_tolerance_seconds=_attempt_reset_at_tolerance(candidate),
                )
                if attempt is None:
                    continue

                send_task = asyncio.create_task(
                    self._dispatch._send_warmup(
                        attempt,
                        account=account,
                        model=model,
                        prompt=settings.limit_warmup_prompt,
                        sender=sender,
                        semaphore=send_semaphore,
                    ),
                    name=f"limit-warmup:{attempt.id}",
                )
                send_tasks[send_task] = attempt

        await self._completion.drain(send_tasks, latest_attempts)


@dataclass(frozen=True, slots=True)
class _WarmupCandidate:
    reset_at: int
    window: str


def _build_candidate(
    *,
    account: Account,
    window: str,
    before_primary: dict[str, UsageHistory],
    before_secondary: dict[str, UsageHistory],
    after_primary: dict[str, UsageHistory],
    after_secondary: dict[str, UsageHistory],
    min_available_percent: float,
) -> _WarmupCandidate | None:
    before = _effective_usage_entry(
        account.id,
        window=window,
        primary=before_primary,
        secondary=before_secondary,
    )
    after = _effective_usage_entry(
        account.id,
        window=window,
        primary=after_primary,
        secondary=after_secondary,
    )
    if before is None or after is None:
        return None
    available_percent = 100.0 - after.used_percent
    if min_available_percent < 100.0 and available_percent < min_available_percent:
        return None
    if not usage_reset_confirmed(before=before, after=after):
        return None
    assert after.reset_at is not None
    candidate_window = "monthly" if after.window == "monthly" else window
    return _WarmupCandidate(reset_at=after.reset_at, window=candidate_window)


def usage_reset_confirmed(*, before: UsageHistory | None, after: UsageHistory | None) -> bool:
    """Return whether consecutive samples prove a real, newly available quota window."""
    if before is None or after is None:
        return False
    if before.reset_at is None or after.reset_at is None:
        return False
    if (before.window or "primary") != (after.window or "primary"):
        return False
    if after.used_percent >= 100.0:
        return False
    reset_at_jump = after.reset_at - before.reset_at
    if reset_at_jump < _RESET_CONFIRMED_MIN_JUMP_SECONDS:
        return False
    before_observed_at = naive_utc_to_epoch(before.recorded_at)
    observed_at = naive_utc_to_epoch(after.recorded_at)
    window_started_at = after.reset_at - _rolling_window_seconds(after)
    quota_recovered = after.used_percent < before.used_percent
    crossed_previous_reset = before_observed_at <= before.reset_at <= observed_at < after.reset_at
    reanchored_between_samples = (
        quota_recovered and before_observed_at <= window_started_at <= observed_at < after.reset_at
    )
    # Scheduled resets cross the previous boundary. Early resets can happen
    # when upstream restores quota and reanchors a complete window from the
    # sampling interval. A reset_at update outside that interval is not a reset.
    if not crossed_previous_reset and not reanchored_between_samples:
        return False
    return True


def _build_paid_to_free_transition_candidate(
    *,
    account: Account,
    previous_plan_type: str | None,
    after_secondary: dict[str, UsageHistory],
    refresh_started_at: datetime | None,
    min_available_percent: float,
) -> _WarmupCandidate | None:
    normalized_previous_plan = normalize_account_plan_type(previous_plan_type)
    if normalized_previous_plan is None or normalized_previous_plan == "free":
        return None
    if normalize_account_plan_type(account.plan_type) != "free":
        return None
    if refresh_started_at is None:
        return None
    after = after_secondary.get(account.id)
    if after is None or after.window != "monthly" or after.reset_at is None:
        return None
    if after.recorded_at < refresh_started_at:
        return None
    if after.used_percent >= 100.0:
        return None
    available_percent = 100.0 - after.used_percent
    if min_available_percent < 100.0 and available_percent < min_available_percent:
        return None
    return _WarmupCandidate(reset_at=after.reset_at, window="monthly")


def _build_staggered_idle_candidate(
    *,
    account: Account,
    accounts: list[Account],
    now: datetime,
    after_primary: dict[str, UsageHistory],
    refresh_started_at: datetime | None,
    usage_refresh_interval_seconds: int,
    idle_threshold_percent: float = 1.0,
) -> _WarmupCandidate | None:
    after = after_primary.get(account.id)
    if after is None:
        return None
    if after.reset_at is None:
        return None
    if after.window_minutes is not None and after.window_minutes > _SHORT_WINDOW_MAX_MINUTES:
        # Any long window (weekly, monthly, or a nonstandard duration over
        # 24h) has no short phase to pre-start.
        return None
    if after.used_percent > idle_threshold_percent:
        return None

    window_seconds = _rolling_window_seconds(after)
    due = _staggered_idle_due(
        account.id,
        [candidate.id for candidate in accounts],
        now=now,
        reset_at=after.reset_at,
        interval_started_at=refresh_started_at,
        usage_refresh_interval_seconds=usage_refresh_interval_seconds,
        window_seconds=window_seconds,
    )
    if due is None:
        return None
    if not _usage_entry_refreshed_for_cycle(
        after,
        refresh_started_at=refresh_started_at,
        cycle_end=due.cycle_end,
        window_seconds=window_seconds,
    ):
        return None
    return _WarmupCandidate(reset_at=after.reset_at, window=_IDLE_PRIMARY_WINDOW)


@dataclass(frozen=True, slots=True)
class _StaggeredIdleDue:
    cycle_end: int
    slot_offset_seconds: int


def _rolling_window_seconds(entry: UsageHistory) -> int:
    # Derive the rolling cycle from the observed primary window duration so
    # the stagger tracks whatever short window upstream reports; 300 minutes
    # remains the fallback when duration metadata is missing.
    if entry.window_minutes is not None and entry.window_minutes > 0:
        return int(entry.window_minutes) * 60
    return _ROLLING_WINDOW_SECONDS


def _staggered_idle_due(
    account_id: str,
    account_ids: list[str],
    *,
    now: datetime,
    reset_at: int,
    interval_started_at: datetime | None = None,
    usage_refresh_interval_seconds: int = _STAGGER_SLOT_GRACE_SECONDS,
    window_seconds: int = _ROLLING_WINDOW_SECONDS,
) -> _StaggeredIdleDue | None:
    if not account_ids:
        return None
    ordered_account_ids = sorted(set(account_ids))
    try:
        account_index = ordered_account_ids.index(account_id)
    except ValueError:
        return None

    now_epoch = naive_utc_to_epoch(now)
    cycle_start = reset_at - window_seconds
    if now_epoch < cycle_start or now_epoch >= reset_at:
        return None
    elapsed = now_epoch - cycle_start
    slot_offset = int(account_index * window_seconds / len(ordered_account_ids))
    grace_seconds = max(_STAGGER_SLOT_GRACE_SECONDS, usage_refresh_interval_seconds)
    interval_start_epoch = naive_utc_to_epoch(interval_started_at) if interval_started_at is not None else now_epoch
    interval_start_epoch = min(interval_start_epoch, now_epoch)
    interval_start_elapsed = max(0, interval_start_epoch - cycle_start)
    if not slot_offset <= elapsed:
        return None
    if slot_offset + grace_seconds <= interval_start_elapsed:
        return None
    return _StaggeredIdleDue(
        cycle_end=cycle_start + window_seconds,
        slot_offset_seconds=slot_offset,
    )


def _usage_entry_refreshed_for_cycle(
    entry: UsageHistory,
    *,
    refresh_started_at: datetime | None,
    cycle_end: int | None,
    window_seconds: int = _ROLLING_WINDOW_SECONDS,
) -> bool:
    if entry.recorded_at is None:
        return False
    if cycle_end is not None:
        cycle_start = cycle_end - window_seconds
        if entry.recorded_at < datetime.fromtimestamp(cycle_start, tz=timezone.utc).replace(tzinfo=None):
            return False
        if entry.reset_at is not None and entry.reset_at <= cycle_start:
            return False
    if refresh_started_at is None:
        return True
    return entry.recorded_at >= refresh_started_at


def _effective_usage_entry(
    account_id: str,
    *,
    window: str,
    primary: dict[str, UsageHistory],
    secondary: dict[str, UsageHistory],
) -> UsageHistory | None:
    if window == "primary":
        primary_entry = primary.get(account_id)
        if primary_entry is None or usage_core.is_weekly_window_minutes(primary_entry.window_minutes):
            return None
        return primary_entry

    primary_entry = primary.get(account_id)
    secondary_entry = secondary.get(account_id)
    if primary_entry is not None and usage_core.is_weekly_window_minutes(primary_entry.window_minutes):
        if secondary_entry is None:
            return primary_entry
        if usage_core.should_use_weekly_primary(
            usage_history_to_window_row(primary_entry),
            usage_history_to_window_row(secondary_entry),
        ):
            return primary_entry
    return secondary_entry


def _event_error(*errors: OpenAIError | None) -> OpenAIError:
    for error in errors:
        if error is not None:
            return error
    return OpenAIError(message=None, code=None)


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _truncate(value: str | None, limit: int = 1000) -> str | None:
    if value is None:
        return None
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "..."


def _attempt_reset_at_tolerance(candidate: _WarmupCandidate) -> int:
    return _RESET_AT_JITTER_TOLERANCE_SECONDS
