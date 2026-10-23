from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.clients import anthropic_resets
from app.core.config.settings import get_settings
from app.core.utils.time import utcnow
from app.db.models import Account
from app.modules.accounts import reset_credit_cache
from app.modules.accounts.auth_manager import is_locally_owned
from app.modules.accounts.reset_credit_attempts import ResetCreditAttemptsRepository
from app.modules.accounts.reset_credit_recovery import refresh_standard_capacity
from app.modules.accounts.schemas import AccountResetCreditConsumeResponse
from app.modules.accounts.service_errors import AccountResetCreditsUnavailableError
from app.modules.proxy.account_cache import get_account_selection_cache


class AnthropicAccountResetService:
    async def _redeem_anthropic_reset(
        self, account: Account, credit_id: str | None, *, trigger: str, override_daily_limit: bool
    ) -> AccountResetCreditConsumeResponse:
        if not is_locally_owned(account, get_settings()):
            raise AccountResetCreditsUnavailableError("Account is not locally owned and cannot use reset credits")
        account_id = account.id
        token = self._encryptor.decrypt(account.access_token_encrypted)
        attempts = self._reset_attempts or ResetCreditAttemptsRepository(self._repo.session)
        primary_before, secondary_before = await self._latest_usage_percents(account_id)
        attempt = await attempts.active()
        if attempt is not None:
            if attempt.account_id != account_id or (credit_id is not None and credit_id != attempt.credit_id):
                raise AccountResetCreditsUnavailableError("Another reset attempt requires reconciliation")
            if not await attempts.acquire(attempt):
                raise AccountResetCreditsUnavailableError("Reset recovery is already running or cooling down")
            if attempt.state != "applied":
                raise AccountResetCreditsUnavailableError(
                    "Claude reset outcome is unresolved; no additional credit will be consumed"
                )
            result_code = attempt.result_code or "reset"
            windows_reset = attempt.windows_reset
        else:
            inventory = await anthropic_resets.fetch_status(access_token=token)
            grant = inventory.usable_grant(datetime.now(timezone.utc), credit_id)
            if grant is None:
                return AccountResetCreditConsumeResponse(
                    status="not_redeemed",
                    account_id=account_id,
                    code="not_eligible",
                    windows_reset=0,
                    primary_used_percent_before=primary_before,
                    primary_used_percent_after=primary_before,
                    secondary_used_percent_before=secondary_before,
                    secondary_used_percent_after=secondary_before,
                )
            attempt_trigger = "manual_override" if override_daily_limit else trigger
            attempt = await attempts.create(account_id, grant.id, attempt_trigger)
            if attempt is None:
                raise AccountResetCreditsUnavailableError("Another reset attempt is already running")
            if not override_daily_limit and await attempts.anthropic_applied_since(utcnow() - timedelta(days=1)):
                await attempts.settle(attempt, "daily_limit")
                raise AccountResetCreditsUnavailableError("One Claude reset was already used in the last 24 hours")
            result = await anthropic_resets.redeem(access_token=token, grant_id=grant.id, request_id=attempt.id)
            result_code = result.result
            windows_reset = len(result.cleared)
            if result.result == "reset":
                await attempts.applied(attempt, result_code, windows_reset)
            elif result.result == "unavailable":
                raise AccountResetCreditsUnavailableError("Claude reset outcome is unresolved; reconciliation required")
            else:
                await attempts.settle(attempt, result_code)
                return AccountResetCreditConsumeResponse(
                    status="not_redeemed",
                    account_id=account_id,
                    code=result_code,
                    windows_reset=0,
                    primary_used_percent_before=primary_before,
                    secondary_used_percent_before=secondary_before,
                )
        reset_credit_cache.clear(account_id)
        if self._usage_repo and self._usage_updater:
            recovered = await refresh_standard_capacity(account, self._usage_updater, self._usage_repo)
            get_account_selection_cache().invalidate()
            if recovered is True:
                await attempts.settle(attempt, result_code)
        primary_after, secondary_after = await self._latest_usage_percents(account_id)
        return AccountResetCreditConsumeResponse(
            status="redeemed",
            account_id=account_id,
            code=result_code,
            windows_reset=windows_reset,
            primary_used_percent_before=primary_before,
            primary_used_percent_after=primary_after,
            secondary_used_percent_before=secondary_before,
            secondary_used_percent_after=secondary_after,
        )
