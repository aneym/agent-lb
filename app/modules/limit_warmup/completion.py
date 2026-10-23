from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from app.core.identity import internal_identity
from app.core.utils.time import utcnow
from app.db.models import Account
from app.db.request_models import AccountLimitWarmup
from app.modules.limit_warmup.constants import (  # noqa: I001  # fmt: skip
    _ANTHROPIC_CONTINUOUS_WINDOW,
    _OPENAI_CONTINUOUS_WINDOW,
    _QUOTA_ERROR_CODES,
    LIMIT_WARMUP_REQUEST_KIND,
    LIMIT_WARMUP_SOURCE,
)
from app.modules.limit_warmup.contracts import (  # noqa: I001  # fmt: skip
    LimitWarmupAttemptsRepository,
    LimitWarmupRequestLogRepository,
    LimitWarmupSendOutcome,
    LimitWarmupSendResult,
)
from app.modules.limit_warmup.policy import _as_utc, _safe_openai_log_error_code, _truncate

logger = logging.getLogger("app.modules.limit_warmup.service")


class WarmupCompletion:
    def __init__(
        self,
        warmup_repo: LimitWarmupAttemptsRepository,
        request_logs_repo: LimitWarmupRequestLogRepository,
        *,
        confirm_anthropic_prime: Callable[[str, int], Awaitable[bool]] | None = None,
    ) -> None:
        self._warmup_repo = warmup_repo
        self._request_logs_repo = request_logs_repo
        self._confirm_anthropic_prime = confirm_anthropic_prime

    async def _complete_warmup(self, outcome: LimitWarmupSendOutcome) -> AccountLimitWarmup | None:
        if outcome.attempt.window == _ANTHROPIC_CONTINUOUS_WINDOW:
            return await self._complete_continuous_anthropic(outcome)
        if outcome.attempt.window == _OPENAI_CONTINUOUS_WINDOW:
            return await self._complete_continuous_openai(outcome)
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

    async def _complete_continuous_anthropic(self, outcome: LimitWarmupSendOutcome) -> AccountLimitWarmup | None:
        result = outcome.result
        if result is None:
            logger.warning(
                "Anthropic primer result account_id=%s reset_at=%s status=failed error_code=warmup_send_failed",
                outcome.account.id,
                outcome.attempt.reset_at,
            )
            return await self._warmup_repo.complete_attempt(
                outcome.attempt.id,
                status="failed",
                completed_at=utcnow(),
                error_code="warmup_send_failed",
                error_message=_truncate(outcome.error_message),
            )

        status = "sent_unconfirmed" if result.success else "failed"
        completed = await self._warmup_repo.complete_attempt(
            outcome.attempt.id,
            status=status,
            completed_at=utcnow(),
            error_code=None if result.success else result.error_code,
            error_message=None if result.success else _truncate(result.error_message),
        )
        try:
            await self._record_request_log(account=outcome.account, model=outcome.model, result=result)
        except Exception as exc:
            logger.warning(
                "Anthropic primer request log failed account_id=%s error_type=%s",
                outcome.account.id,
                type(exc).__name__,
            )
        if result.success and self._confirm_anthropic_prime is not None:
            try:
                sent_at = int(_as_utc(outcome.attempt.attempted_at).timestamp())
                if await self._confirm_anthropic_prime(outcome.account.id, sent_at):
                    confirmed_row = await self._warmup_repo.complete_attempt(
                        outcome.attempt.id,
                        status="succeeded",
                        completed_at=utcnow(),
                    )
                    completed = confirmed_row or completed
                    status = "succeeded"
            except Exception as exc:
                logger.warning(
                    "Anthropic primer confirmation failed account_id=%s reset_at=%s error_type=%s",
                    outcome.account.id,
                    outcome.attempt.reset_at,
                    type(exc).__name__,
                )
        logger.log(
            logging.INFO if result.success else logging.WARNING,
            "Anthropic primer result account_id=%s reset_at=%s status=%s error_code=%s",
            outcome.account.id,
            outcome.attempt.reset_at,
            status,
            result.error_code,
        )
        return completed

    async def _complete_continuous_openai(self, outcome: LimitWarmupSendOutcome) -> AccountLimitWarmup | None:
        result = outcome.result
        if result is None:
            logger.warning(
                "OpenAI primer result account_id=%s bucket=%s status=failed error_code=warmup_send_failed",
                outcome.account.id,
                outcome.attempt.reset_at,
            )
            return await self._warmup_repo.complete_attempt(
                outcome.attempt.id,
                status="failed",
                completed_at=utcnow(),
                error_code="warmup_send_failed",
                error_message=_truncate(outcome.error_message),
            )

        status = "succeeded" if result.success else "failed"
        error_code = "quota_still_exhausted" if result.error_code in _QUOTA_ERROR_CODES else result.error_code
        completed = await self._warmup_repo.complete_attempt(
            outcome.attempt.id,
            status=status,
            completed_at=utcnow(),
            error_code=error_code,
            error_message=_truncate(result.error_message),
        )
        try:
            await self._record_request_log(account=outcome.account, model=outcome.model, result=result)
        except Exception as exc:
            logger.warning(
                "OpenAI primer request log failed account_id=%s error_type=%s",
                outcome.account.id,
                type(exc).__name__,
            )
        logger.log(
            logging.INFO if result.success else logging.WARNING,
            "OpenAI primer result account_id=%s bucket=%s status=%s error_code=%s",
            outcome.account.id,
            outcome.attempt.reset_at,
            status,
            _safe_openai_log_error_code(result.error_code),
        )
        return completed

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
            identity=internal_identity(),
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

    async def drain(
        self,
        send_tasks: dict[asyncio.Task[LimitWarmupSendOutcome], AccountLimitWarmup],
        latest_attempts: dict[str, AccountLimitWarmup],
    ) -> None:
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
