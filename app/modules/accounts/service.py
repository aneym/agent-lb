from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.core.clients import anthropic_resets, rate_limit_resets
from app.core.config.settings import get_settings
from app.core.providers import ANTHROPIC_PROVIDER_NAME, OPENAI_PROVIDER_NAME, normalize_provider_name
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.modules.accounts import reset_credit_cache
from app.modules.accounts.auth_contracts import is_locally_owned
from app.modules.accounts.lifecycle import AccountsLifecycle
from app.modules.accounts.reset_credit_attempts import ResetCreditAttemptsRepository
from app.modules.accounts.reset_credit_recovery import refresh_standard_capacity
from app.modules.accounts.schemas import (
    AccountResetCredit,
    AccountResetCreditConsumeResponse,
    AccountResetCreditsResponse,
)
from app.modules.accounts.subscription_status import is_subscription_usable
from app.modules.proxy.account_cache import get_account_selection_cache


class AccountResetCreditsUnavailableError(Exception):
    pass


class AccountsService(AccountsLifecycle):
    async def list_rate_limit_reset_credits(self, account_id: str) -> AccountResetCreditsResponse | None:
        account = await self._repo.get_by_id(account_id)
        if account is None:
            return None
        credit_account = await self._reset_credit_account(account)
        access_token = self._encryptor.decrypt(credit_account.access_token_encrypted)
        if normalize_provider_name(credit_account.provider) == ANTHROPIC_PROVIDER_NAME:
            try:
                inventory = await anthropic_resets.fetch_status(access_token=access_token)
            except rate_limit_resets.ResetCreditsError:
                reset_credit_cache.clear(account_id)
                raise
            now = datetime.now(timezone.utc)
            credits = [
                AccountResetCredit(
                    id=grant.id,
                    reset_type="claude_banked",
                    status="available"
                    if (
                        grant.resets_left > 0
                        and (grant.ends_at is None or anthropic_resets._utc(grant.ends_at) > now)
                        and (grant.starts_at is None or anthropic_resets._utc(grant.starts_at) <= now)
                    )
                    else "unavailable",
                    granted_at=grant.starts_at.isoformat() if grant.starts_at else "",
                    expires_at=grant.ends_at.isoformat() if grant.ends_at else None,
                    title=grant.label,
                )
                for grant in inventory.grants
            ]
            available_count = sum(
                grant.resets_left for grant, credit in zip(inventory.grants, credits) if credit.status == "available"
            )
            reset_credit_cache.record_count(account_id, available_count)
            redeemable_now = inventory.usable_grant(now) is not None
            return AccountResetCreditsResponse(
                account_id=account_id,
                available_count=available_count,
                credits=credits,
                redeemable_now=redeemable_now,
                ineligible_reason=None if redeemable_now else inventory.redemption_blocker(now),
            )
        payload = await rate_limit_resets.fetch_reset_credits(
            access_token=access_token,
            chatgpt_account_id=credit_account.chatgpt_account_id,
        )
        reset_credit_cache.record_count(
            account_id,
            sum(credit.status == "available" for credit in payload.credits),
        )
        return AccountResetCreditsResponse(
            account_id=account_id,
            available_count=payload.available_count,
            credits=[AccountResetCredit(**credit.model_dump()) for credit in payload.credits],
        )

    async def redeem_rate_limit_reset_credit(
        self,
        account_id: str,
        credit_id: str | None = None,
        *,
        trigger: str = "manual",
        override_daily_limit: bool = False,
    ) -> AccountResetCreditConsumeResponse | None:

        account = await self._repo.get_by_id(account_id)
        if account is None:
            return None
        if not is_locally_owned(account, get_settings()):
            raise AccountResetCreditsUnavailableError("Account is not locally owned and cannot use reset credits")
        if override_daily_limit and trigger != "manual":
            raise AccountResetCreditsUnavailableError("Only manual redemption may override the Claude daily limit")
        if override_daily_limit and normalize_provider_name(account.provider) != ANTHROPIC_PROVIDER_NAME:
            raise AccountResetCreditsUnavailableError("Daily-limit override is available only for Claude resets")
        credit_account = await self._reset_credit_account(account)
        if normalize_provider_name(credit_account.provider) == ANTHROPIC_PROVIDER_NAME:
            return await self._redeem_anthropic_reset(
                credit_account, credit_id, trigger=trigger, override_daily_limit=override_daily_limit
            )

        primary_before, secondary_before = await self._latest_usage_percents(account_id)
        access_token = self._encryptor.decrypt(credit_account.access_token_encrypted)
        attempts = self._reset_attempts or ResetCreditAttemptsRepository(self._repo.session)
        attempt = await attempts.active()
        retrying = attempt is not None
        if attempt is not None:
            if attempt.account_id != account_id or (credit_id is not None and attempt.credit_id != credit_id):
                raise AccountResetCreditsUnavailableError("Another reset attempt requires reconciliation")
            if not await attempts.acquire(attempt):
                raise AccountResetCreditsUnavailableError("Reset recovery is already running or cooling down")
        else:
            inventory = await rate_limit_resets.fetch_reset_credits(
                access_token=access_token,
                chatgpt_account_id=credit_account.chatgpt_account_id,
            )
            available = [item for item in inventory.credits if item.status == "available"]
            reset_credit_cache.record_count(account_id, len(available))
            if (
                inventory.available_count <= 0
                or not available
                or (credit_id is not None and not any(item.id == credit_id for item in available))
            ):
                return AccountResetCreditConsumeResponse(
                    status="not_redeemed",
                    account_id=account_id,
                    code="no_credit",
                    windows_reset=0,
                    primary_used_percent_before=primary_before,
                    secondary_used_percent_before=secondary_before,
                    primary_used_percent_after=primary_before,
                    secondary_used_percent_after=secondary_before,
                )
            if credit_id is None:
                credit_id = min(available, key=lambda item: item.expires_at or "9999").id
            attempt = await attempts.create(account_id, credit_id, trigger)
            if attempt is None:
                raise AccountResetCreditsUnavailableError("Another reset attempt is already running")

        if attempt.state == "applied":
            payload = rate_limit_resets.ConsumeResetCreditPayload(
                code="already_redeemed",
                windows_reset=attempt.windows_reset,
            )
        else:
            inventory = await rate_limit_resets.fetch_reset_credits(
                access_token=access_token,
                chatgpt_account_id=credit_account.chatgpt_account_id,
            )
            available = [item for item in inventory.credits if item.status == "available"]
            reset_credit_cache.record_count(account_id, len(available))
            credit = next((item for item in inventory.credits if item.id == attempt.credit_id), None)
            if credit is not None and credit.status == "redeemed":
                payload = rate_limit_resets.ConsumeResetCreditPayload(code="already_redeemed")
            elif inventory.available_count <= 0 and attempt.credit_id is None:
                payload = rate_limit_resets.ConsumeResetCreditPayload(code="no_credit")
            elif credit is not None and credit.status not in {"available", "redeemed"}:
                raise AccountResetCreditsUnavailableError("Reset credit status is unresolved; outcome is unknown")
            elif attempt.credit_id is not None and credit is None:
                raise AccountResetCreditsUnavailableError("Reset credit is absent from inventory; outcome is unknown")
            else:
                if self._usage_updater and self._usage_repo:
                    refreshed = await self._usage_updater.force_refresh(credit_account)
                    if retrying and not refreshed:
                        raise AccountResetCreditsUnavailableError("Pending reset requires fresh usage reconciliation")
                if inventory.available_count <= 0:
                    raise AccountResetCreditsUnavailableError("Reset inventory has no available credits")
                payload = await rate_limit_resets.consume_reset_credit(
                    access_token=access_token,
                    chatgpt_account_id=credit_account.chatgpt_account_id,
                    redeem_request_id=attempt.id,
                    credit_id=attempt.credit_id,
                )

            if payload.code in ("reset", "already_redeemed"):
                await attempts.applied(attempt, payload.code, payload.windows_reset)
            else:
                await attempts.settle(attempt, payload.code)

        if payload.code in ("reset", "already_redeemed"):
            try:
                refreshed_credits = await rate_limit_resets.fetch_reset_credits(
                    access_token=access_token,
                    chatgpt_account_id=credit_account.chatgpt_account_id,
                )
                reset_credit_cache.record_count(
                    account_id,
                    sum(credit.status == "available" for credit in refreshed_credits.credits),
                )
            except Exception:
                reset_credit_cache.clear(account_id)
                logging.getLogger(__name__).warning(
                    "Post-redemption reset-credit listing failed account=%s",
                    account_id,
                    exc_info=True,
                )

            if self._usage_repo and self._usage_updater:
                recovered = await refresh_standard_capacity(credit_account, self._usage_updater, self._usage_repo)
                get_account_selection_cache().invalidate()
                if recovered is not True:
                    await self.probe_account(account_id)
                    recovered = await refresh_standard_capacity(credit_account, self._usage_updater, self._usage_repo)
                if recovered is True:
                    await attempts.settle(attempt, payload.code)

        primary_after, secondary_after = await self._latest_usage_percents(account_id)
        return AccountResetCreditConsumeResponse(
            status="redeemed" if payload.code in ("reset", "already_redeemed") else "not_redeemed",
            account_id=account_id,
            code=payload.code,
            windows_reset=payload.windows_reset,
            primary_used_percent_before=primary_before,
            primary_used_percent_after=primary_after,
            secondary_used_percent_before=secondary_before,
            secondary_used_percent_after=secondary_after,
        )

    async def _redeem_anthropic_reset(
        self,
        account: Account,
        credit_id: str | None,
        *,
        trigger: str,
        override_daily_limit: bool,
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

    async def _reset_credit_account(self, account: Account) -> Account:
        provider = normalize_provider_name(account.provider)
        if provider not in (OPENAI_PROVIDER_NAME, ANTHROPIC_PROVIDER_NAME):
            raise AccountResetCreditsUnavailableError(f"Provider {provider} does not support rate-limit reset credits")
        if account.status in (
            AccountStatus.PAUSED,
            AccountStatus.REAUTH_REQUIRED,
            AccountStatus.DEACTIVATED,
            AccountStatus.EXCHANGE_UNCERTAIN,
        ):
            raise AccountResetCreditsUnavailableError(f"Account is {account.status.value} and cannot use reset credits")
        if not is_subscription_usable(account):
            raise AccountResetCreditsUnavailableError("Account subscription cannot use reset credits")
        if self._auth_manager is not None:
            return await self._auth_manager.ensure_fresh(account, force=False)
        return account
