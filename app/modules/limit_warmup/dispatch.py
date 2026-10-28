from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from app.core.account_warmup import warmup_active
from app.core.openai.model_registry import get_model_registry
from app.core.plan_types import account_plan_matches_allowed
from app.core.providers import ANTHROPIC_PROVIDER_NAME
from app.core.usage.pricing import get_pricing_for_model
from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import Account, DashboardSettings
from app.db.request_models import AccountLimitWarmup
from app.modules.limit_warmup.constants import _ANTHROPIC_CONTINUOUS_WINDOW, _ANTHROPIC_PRIMER_PROMPT, _DEFAULT_ANTHROPIC_WARMUP_MODEL, _OPENAI_CONTINUOUS_WINDOW  # noqa: I001  # fmt: skip
from app.modules.limit_warmup.contracts import LimitWarmupAttemptsRepository, LimitWarmupSender, LimitWarmupSendOutcome

logger = logging.getLogger("app.modules.limit_warmup.service")


class WarmupDispatch:
    def __init__(self, warmup_repo: LimitWarmupAttemptsRepository) -> None:
        self._warmup_repo = warmup_repo

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
        now: datetime | None = None,
    ) -> tuple[asyncio.Task[LimitWarmupSendOutcome], AccountLimitWarmup] | None:
        if warmup_active(account):
            return None
        continuous = window in {_ANTHROPIC_CONTINUOUS_WINDOW, _OPENAI_CONTINUOUS_WINDOW}
        model = (
            _DEFAULT_ANTHROPIC_WARMUP_MODEL
            if window == _ANTHROPIC_CONTINUOUS_WINDOW
            else self._resolve_model(settings.limit_warmup_model, account)
        )
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

        if continuous:
            attempt = await self._warmup_repo.claim_continuous_attempt(
                account_id=account.id,
                reset_at=reset_at,
                model=model,
                now=to_utc_naive(now) if now is not None else utcnow(),
                window=window,
            )
        else:
            attempt = await self._warmup_repo.try_create_attempt(
                account_id=account.id, window=window, reset_at=reset_at, model=model, attempted_at=utcnow()
            )
        if attempt is None:
            return None

        if window == _ANTHROPIC_CONTINUOUS_WINDOW:
            logger.info("Anthropic primer attempt account_id=%s reset_at=%s", account.id, reset_at)
        elif window == _OPENAI_CONTINUOUS_WINDOW:
            logger.info("OpenAI primer attempt account_id=%s bucket=%s", account.id, reset_at)

        send_task = asyncio.create_task(
            self._send_warmup(
                attempt,
                account=account,
                model=model,
                prompt=(
                    _ANTHROPIC_PRIMER_PROMPT if window == _ANTHROPIC_CONTINUOUS_WINDOW else settings.limit_warmup_prompt
                ),
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
            if attempt.window in {_ANTHROPIC_CONTINUOUS_WINDOW, _OPENAI_CONTINUOUS_WINDOW}:
                provider = "Anthropic" if attempt.window == _ANTHROPIC_CONTINUOUS_WINDOW else "OpenAI"
                logger.warning(
                    "%s primer send exception account_id=%s error_type=%s", provider, account.id, type(exc).__name__
                )
            else:
                logger.warning(
                    "Limit warm-up send failed account_id=%s window=%s", account.id, attempt.window, exc_info=True
                )
            return LimitWarmupSendOutcome(
                attempt=attempt,
                account=account,
                model=model,
                result=None,
                error_message=(
                    type(exc).__name__
                    if attempt.window in {_ANTHROPIC_CONTINUOUS_WINDOW, _OPENAI_CONTINUOUS_WINDOW}
                    else str(exc)
                ),
            )

        return LimitWarmupSendOutcome(attempt=attempt, account=account, model=model, result=result)
