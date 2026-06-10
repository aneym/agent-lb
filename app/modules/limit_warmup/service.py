from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import time as dt_time
from typing import AsyncContextManager, Callable, Protocol

from app.core import usage as usage_core
from app.core.auth.refresh import RefreshError
from app.core.clients.proxy import UpstreamProxyRouteTrace, override_stream_timeouts, stream_responses
from app.core.crypto import TokenEncryptor
from app.core.openai.model_registry import get_model_registry
from app.core.openai.models import OpenAIError, ResponseUsage
from app.core.openai.parsing import parse_sse_event
from app.core.openai.requests import ResponsesRequest
from app.core.plan_types import account_plan_matches_allowed
from app.core.providers import ANTHROPIC_PROVIDER_NAME
from app.core.upstream_proxy import ResolvedUpstreamRoute, UpstreamProxyRouteError, resolve_upstream_route
from app.core.usage.pricing import get_pricing_for_model
from app.core.utils.time import utcnow
from app.db.models import Account, AccountLimitWarmup, AccountStatus, DashboardSettings, UsageHistory
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository
from app.modules.limit_warmup.anthropic_primer import send_anthropic_primer
from app.modules.quota_planner.logic import PlannerSettings, _parse_hhmm, _to_planner_tz
from app.modules.usage.mappers import usage_history_to_window_row

logger = logging.getLogger(__name__)

LIMIT_WARMUP_SOURCE = "limit_warmup"
LIMIT_WARMUP_REQUEST_KIND = "warmup"
LIMIT_WARMUP_HEADER = "x-agent-lb-limit-warmup"
_DEFAULT_WARMUP_INSTRUCTIONS = "Reply with OK only."
_DEFAULT_ANTHROPIC_WARMUP_MODEL = "claude-haiku-4-5"
_DEFAULT_PRIMARY_WINDOW_MINUTES = 300
_SEED_WINDOW = "primary"
_DEFAULT_WORKING_HOURS_START = dt_time(9, 0)
_DEFAULT_WORKING_HOURS_END = dt_time(18, 0)
_TERMINAL_ERROR_EVENTS = {"response.failed", "response.incomplete", "error"}
_QUOTA_ERROR_CODES = {"insufficient_quota", "quota_exceeded", "rate_limit_exceeded", "usage_limit_reached"}
_MAX_CONCURRENT_WARMUP_SENDS = 4


@dataclass(frozen=True, slots=True)
class LimitWarmupSendResult:
    request_id: str
    success: bool
    latency_ms: int
    usage: ResponseUsage | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    upstream_proxy_route_mode: str | None = None
    upstream_proxy_pool_id: str | None = None
    upstream_proxy_endpoint_id: str | None = None
    upstream_proxy_fallback_used: bool | None = None
    upstream_proxy_fail_closed_reason: str | None = None


@dataclass(frozen=True, slots=True)
class LimitWarmupSendOutcome:
    attempt: AccountLimitWarmup
    account: Account
    model: str
    result: LimitWarmupSendResult | None
    error_message: str | None = None


class LimitWarmupSender(Protocol):
    async def send(self, account: Account, *, model: str, prompt: str) -> LimitWarmupSendResult: ...


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
        api_key_id: str | None = None,
        session_id: str | None = None,
        plan_type: str | None = None,
        source: str | None = None,
        useragent: str | None = None,
        useragent_group: str | None = None,
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
    ) -> None:
        self._warmup_repo = warmup_repo
        self._request_logs_repo = request_logs_repo
        self._sender = sender

    async def run_after_usage_refresh(
        self,
        *,
        accounts: list[Account],
        settings: DashboardSettings,
        before_primary: dict[str, UsageHistory],
        before_secondary: dict[str, UsageHistory],
        after_primary: dict[str, UsageHistory],
        after_secondary: dict[str, UsageHistory],
        planner_settings: PlannerSettings | None = None,
        now: datetime | None = None,
    ) -> None:
        if not settings.limit_warmup_enabled:
            return
        selected_windows = _selected_windows(settings.limit_warmup_windows)
        # The clock-aware seed path (Behavior 1) only needs a working-hours
        # schedule, so it can run even when no reactive HOLD windows are
        # selected. Without a schedule the service keeps its legacy behavior.
        if not selected_windows and planner_settings is None:
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
            if _in_cooldown(
                latest_attempt,
                cooldown_seconds=settings.limit_warmup_cooldown_seconds,
            ):
                continue

            window_minutes = _primary_window_minutes(account, after_primary.get(account.id))

            # Behavior 1 (SEED): a cold account inside the pre-work seed band gets
            # exactly one primer that anchors its primary window to ~start. This is
            # mutually exclusive with the reactive HOLD path below: a cold account
            # never has an observed reset to react to.
            if planner_settings is not None and _within_seed_window(current, planner_settings, window_minutes):
                if _primary_window_is_cold(account, after_primary.get(account.id), current):
                    enqueued = await self._enqueue_warmup_send(
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

            # Behavior 2 (HOLD): the existing reactive re-prime, now gated to the
            # working day. Outside [seed_start, working_hours_end] the account is
            # allowed to go cold so the next morning's seed re-establishes phase.
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

                enqueued = await self._enqueue_warmup_send(
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

        pending_send_tasks = set(send_tasks)
        try:
            while pending_send_tasks:
                completed_send_tasks, pending_send_tasks = await asyncio.wait(
                    pending_send_tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                completion_error: BaseException | None = None
                for send_task in completed_send_tasks:
                    outcome = await send_task
                    try:
                        completed = await self._complete_warmup(outcome)
                    except Exception as exc:
                        completion_error = completion_error or exc
                        await self._mark_aborted_warmup(
                            outcome.attempt,
                            error_code="warmup_completion_failed",
                            error_message="Limit warm-up completion failed",
                        )
                        continue
                    latest_attempts[outcome.account.id] = completed or outcome.attempt
                if completion_error is not None:
                    raise completion_error
        finally:
            if pending_send_tasks:
                for send_task in pending_send_tasks:
                    send_task.cancel()
                drained_results = await asyncio.gather(*pending_send_tasks, return_exceptions=True)
                for send_task, drained_result in zip(pending_send_tasks, drained_results, strict=True):
                    if isinstance(drained_result, LimitWarmupSendOutcome):
                        try:
                            completed = await self._complete_warmup(drained_result)
                        except Exception:
                            await self._mark_aborted_warmup(
                                drained_result.attempt,
                                error_code="warmup_completion_failed",
                                error_message="Limit warm-up completion failed",
                            )
                            continue
                        latest_attempts[drained_result.account.id] = completed or drained_result.attempt
                        continue
                    await self._mark_aborted_warmup(
                        send_tasks[send_task],
                        error_code=(
                            "warmup_cancelled"
                            if isinstance(drained_result, asyncio.CancelledError)
                            else "warmup_send_failed"
                        ),
                        error_message=(
                            "Limit warm-up cancelled after another warm-up completion failed"
                            if isinstance(drained_result, asyncio.CancelledError)
                            else (_truncate(str(drained_result)) or "Limit warm-up send failed")
                        ),
                    )

    async def _enqueue_warmup_send(
        self,
        *,
        account: Account,
        window: str,
        reset_at: int,
        settings: DashboardSettings,
        sender: LimitWarmupSender,
        semaphore: asyncio.Semaphore,
        latest_attempts: dict[str, AccountLimitWarmup],
    ) -> tuple[asyncio.Task[LimitWarmupSendOutcome], AccountLimitWarmup] | None:
        model = self._resolve_model(settings.limit_warmup_model, account)
        if model is None:
            skipped = await self._warmup_repo.try_create_attempt(
                account_id=account.id,
                window=window,
                reset_at=reset_at,
                model="auto",
                attempted_at=utcnow(),
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
            return None

        attempt = await self._warmup_repo.try_create_attempt(
            account_id=account.id,
            window=window,
            reset_at=reset_at,
            model=model,
            attempted_at=utcnow(),
        )
        if attempt is None:
            return None

        send_task = asyncio.create_task(
            self._send_warmup(
                attempt,
                account=account,
                model=model,
                prompt=settings.limit_warmup_prompt,
                sender=sender,
                semaphore=semaphore,
            ),
            name=f"limit-warmup:{attempt.id}",
        )
        return send_task, attempt

    def _resolve_model(self, configured_model: str, account: Account) -> str | None:
        normalized = configured_model.strip()
        if account.provider == ANTHROPIC_PROVIDER_NAME:
            if normalized and normalized.lower() != "auto" and "claude" in normalized.lower():
                return normalized
            return _DEFAULT_ANTHROPIC_WARMUP_MODEL
        if normalized and normalized.lower() != "auto":
            return normalized

        candidates: list[tuple[float, str]] = []
        for model in get_model_registry().get_models_with_fallback().values():
            if not model.supported_in_api:
                continue
            if model.input_modalities and "text" not in {modality.lower() for modality in model.input_modalities}:
                continue
            if model.available_in_plans and not account_plan_matches_allowed(
                account.plan_type, model.available_in_plans
            ):
                continue
            resolved_price = get_pricing_for_model(model.slug)
            if resolved_price is None:
                continue
            _, price = resolved_price
            candidates.append((price.input_per_1m + price.output_per_1m, model.slug))
        if not candidates:
            return None
        return min(candidates, key=lambda item: (item[0], item[1]))[1]

    async def _send_warmup(
        self,
        attempt: AccountLimitWarmup,
        *,
        account: Account,
        model: str,
        prompt: str,
        sender: LimitWarmupSender,
        semaphore: asyncio.Semaphore,
    ) -> LimitWarmupSendOutcome:
        try:
            async with semaphore:
                result = await sender.send(account, model=model, prompt=prompt)
        except Exception as exc:
            logger.warning(
                "Limit warm-up send failed account_id=%s window=%s", account.id, attempt.window, exc_info=True
            )
            return LimitWarmupSendOutcome(
                attempt=attempt,
                account=account,
                model=model,
                result=None,
                error_message=str(exc),
            )

        return LimitWarmupSendOutcome(attempt=attempt, account=account, model=model, result=result)

    async def _complete_warmup(self, outcome: LimitWarmupSendOutcome) -> AccountLimitWarmup | None:
        if outcome.result is None:
            return await self._warmup_repo.complete_attempt(
                outcome.attempt.id,
                status="failed",
                completed_at=utcnow(),
                error_code="warmup_send_failed",
                error_message=_truncate(outcome.error_message),
            )

        result = outcome.result
        await self._record_request_log(
            account=outcome.account,
            model=outcome.model,
            result=result,
        )
        status = "succeeded" if result.success else "failed"
        error_code = result.error_code
        if error_code in _QUOTA_ERROR_CODES:
            error_code = "quota_still_exhausted"
        return await self._warmup_repo.complete_attempt(
            outcome.attempt.id,
            status=status,
            completed_at=utcnow(),
            error_code=error_code,
            error_message=_truncate(result.error_message),
        )

    async def _mark_aborted_warmup(
        self,
        attempt: AccountLimitWarmup,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        try:
            await self._warmup_repo.complete_attempt(
                attempt.id,
                status="failed",
                completed_at=utcnow(),
                error_code=error_code,
                error_message=error_message,
            )
        except Exception:
            logger.warning(
                "Failed to mark aborted limit warm-up attempt_id=%s error_code=%s",
                attempt.id,
                error_code,
                exc_info=True,
            )

    async def _record_request_log(
        self,
        *,
        account: Account,
        model: str,
        result: LimitWarmupSendResult,
    ) -> None:
        usage = result.usage
        input_tokens = (
            result.input_tokens
            if result.input_tokens is not None
            else (usage.input_tokens if usage is not None else None)
        )
        output_tokens = (
            result.output_tokens
            if result.output_tokens is not None
            else (usage.output_tokens if usage is not None else None)
        )
        cached_input_tokens = (
            result.cached_input_tokens
            if result.cached_input_tokens is not None
            else (
                usage.input_tokens_details.cached_tokens
                if usage is not None and usage.input_tokens_details is not None
                else None
            )
        )
        reasoning_tokens = (
            usage.output_tokens_details.reasoning_tokens
            if usage is not None and usage.output_tokens_details is not None
            else None
        )
        await self._request_logs_repo.add_log(
            account_id=account.id,
            request_id=result.request_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            reasoning_tokens=reasoning_tokens,
            latency_ms=result.latency_ms,
            status="success" if result.success else "error",
            error_code=result.error_code,
            error_message=_truncate(result.error_message),
            transport="http",
            plan_type=account.plan_type,
            source=LIMIT_WARMUP_SOURCE,
            request_kind=LIMIT_WARMUP_REQUEST_KIND,
            upstream_proxy_route_mode=result.upstream_proxy_route_mode,
            upstream_proxy_pool_id=result.upstream_proxy_pool_id,
            upstream_proxy_endpoint_id=result.upstream_proxy_endpoint_id,
            upstream_proxy_fallback_used=result.upstream_proxy_fallback_used,
            upstream_proxy_fail_closed_reason=result.upstream_proxy_fail_closed_reason,
        )


@dataclass(frozen=True, slots=True)
class _WarmupCandidate:
    reset_at: int


def _selected_windows(value: str) -> tuple[str, ...]:
    normalized = value.strip().lower()
    if normalized == "both":
        return ("primary", "secondary")
    if normalized in {"primary", "secondary"}:
        return (normalized,)
    return ()


def _account_is_safe_candidate(account: Account) -> bool:
    return account.status == AccountStatus.ACTIVE


def _in_cooldown(attempt: AccountLimitWarmup | None, *, cooldown_seconds: int) -> bool:
    if attempt is None:
        return False
    return utcnow() - attempt.attempted_at < timedelta(seconds=cooldown_seconds)


def _primary_window_minutes(account: Account, primary_entry: UsageHistory | None) -> int:
    """Resolve the account's primary-window length in minutes.

    The brief references ``account.window_minutes_primary``; that column does
    not exist on ``Account`` (it is derived from the primary ``UsageHistory``
    row). Prefer an explicit attribute if a future schema adds one, then the
    observed primary window, then the conventional 5-hour default.
    """
    explicit = getattr(account, "window_minutes_primary", None)
    if isinstance(explicit, int) and explicit > 0:
        return explicit
    if primary_entry is not None and primary_entry.window_minutes:
        return int(primary_entry.window_minutes)
    return _DEFAULT_PRIMARY_WINDOW_MINUTES


def _primary_window_is_cold(
    account: Account,
    primary_entry: UsageHistory | None,
    current: datetime,
) -> bool:
    """A primary window is cold when there is no reset in the future."""
    reset_at = primary_entry.reset_at if primary_entry is not None else None
    if reset_at is None:
        reset_at = getattr(account, "reset_at", None)
    if reset_at is None:
        return True
    return float(reset_at) <= current.timestamp()


def _planner_day_bounds(
    current: datetime,
    planner: PlannerSettings,
    window_minutes: int,
) -> tuple[datetime, datetime, datetime, datetime, datetime]:
    """Return (local_now, seed_start, work_start, work_end, target_reset) in planner tz.

    ``target_reset`` is ``working_hours_start + seed_target_offset_minutes`` — the
    moment we want the seeded window to reset. We fire the seed one window-length
    earlier (``seed_start``) so a cold account's fresh window expires at the target.
    Offsetting the reset past start (default +120m) lets the account present a
    loaded window at start AND still cycle twice within a ~9h day (3 windows vs 2).
    """
    local_now = _to_planner_tz(current, planner.timezone)
    work_start = datetime.combine(
        local_now.date(),
        _parse_hhmm(planner.working_hours_start, _DEFAULT_WORKING_HOURS_START),
        tzinfo=local_now.tzinfo,
    )
    work_end = datetime.combine(
        local_now.date(),
        _parse_hhmm(planner.working_hours_end, _DEFAULT_WORKING_HOURS_END),
        tzinfo=local_now.tzinfo,
    )
    target_reset = work_start + timedelta(minutes=max(0, planner.seed_target_offset_minutes))
    seed_start = target_reset - timedelta(minutes=max(0, window_minutes))
    return local_now, seed_start, work_start, work_end, target_reset


def _within_seed_window(current: datetime, planner: PlannerSettings, window_minutes: int) -> bool:
    """now ∈ [target_reset − window_minutes, working_hours_start).

    Seeding only fires before the workday begins so a loaded window greets the
    user; the window-length lead means the fresh window resets at ``target_reset``.
    """
    local_now, seed_start, work_start, _, _ = _planner_day_bounds(current, planner, window_minutes)
    return seed_start <= local_now < work_start


def _within_hold_window(current: datetime, planner: PlannerSettings, window_minutes: int) -> bool:
    """now ∈ [seed_start, working_hours_end]."""
    local_now, seed_start, _, work_end, _ = _planner_day_bounds(current, planner, window_minutes)
    return seed_start <= local_now <= work_end


def _seed_reset_at(current: datetime, planner: PlannerSettings) -> int:
    """Epoch seconds of today's target reset (working_hours_start + offset) in planner tz.

    Used as the deterministic ``reset_at`` anchor for the seed attempt so the
    ``(account_id, window, reset_at)`` uniqueness constraint prevents a second
    seed on the same working day. Independent of ``window_minutes`` since the
    target is a fixed clock time.
    """
    _, _, _, _, target_reset = _planner_day_bounds(current, planner, _DEFAULT_PRIMARY_WINDOW_MINUTES)
    return int(target_reset.timestamp())


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
    if before.reset_at is None or after.reset_at is None:
        return None
    if before.used_percent < 100.0:
        return None
    if after.used_percent >= 100.0:
        return None
    available_percent = 100.0 - after.used_percent
    if min_available_percent < 100.0 and available_percent < min_available_percent:
        return None
    if after.reset_at <= before.reset_at:
        return None
    return _WarmupCandidate(reset_at=after.reset_at)


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
