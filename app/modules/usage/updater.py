from __future__ import annotations

import time
from typing import Mapping

from app.core.config.settings import get_settings
from app.core.utils.request_id import get_request_id
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.usage_models import UsageHistory
from app.modules.accounts.subscription_status import is_subscription_usable
from app.modules.usage.account_usage_refresh import UsageAccountRefresh
from app.modules.usage.refresh_ports import AccountRefreshResult
from app.modules.usage.refresh_rules import (
    USAGE_REFRESH_PROVIDERS,
    USAGE_REFRESH_SINGLEFLIGHT,
    clear_usage_refresh_auth_cooldown,
    is_usage_refresh_in_cooldown,
    last_successful_refresh,
    latest_usage_is_fresh,
    logger,
    prune_usage_refresh_auth_cooldowns,
    quota_recovery_should_bypass_freshness,
    usage_refresh_auth_cooldowns,
    usage_refresh_rate_limit_strikes,
)


class UsageUpdater(UsageAccountRefresh):
    async def refresh_accounts(
        self,
        accounts: list[Account],
        latest_usage: Mapping[str, UsageHistory],
    ) -> bool:

        settings = get_settings()
        if not settings.usage_refresh_enabled:
            return False

        refreshed = False
        now = utcnow()
        interval = settings.usage_refresh_interval_seconds
        prune_usage_refresh_auth_cooldowns()
        for account in accounts:
            if account.provider not in USAGE_REFRESH_PROVIDERS:
                continue
            if account.status in (AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED):
                continue
            if is_usage_refresh_in_cooldown(account.id):
                continue
            latest = await self._freshness_usage_entry(account, latest_usage.get(account.id))
            bypass_freshness = quota_recovery_should_bypass_freshness(account, latest=latest)
            if not bypass_freshness and latest_usage_is_fresh(latest, now=now, interval_seconds=interval):
                continue

            if latest is None:
                last_ok = last_successful_refresh.get(account.id)
                if not bypass_freshness and last_ok and (now - last_ok).total_seconds() < interval:
                    continue
                if self._additional_usage_repo is not None:
                    additional_fresh_at = await self._additional_usage_repo.latest_recorded_at_for_account(
                        account.id,
                    )
                    if (
                        not bypass_freshness
                        and additional_fresh_at
                        and (now - additional_fresh_at).total_seconds() < interval
                    ):
                        last_successful_refresh[account.id] = additional_fresh_at
                        continue

            try:
                result = await USAGE_REFRESH_SINGLEFLIGHT.run(
                    account.id,
                    lambda account=account: self._refresh_account_if_stale(
                        account,
                        usage_account_id=account.chatgpt_account_id,
                        interval_seconds=interval,
                    ),
                )
                await self._sync_account_from_repo(account)
                refreshed = refreshed or result.usage_written

                if result.fetch_succeeded:
                    last_successful_refresh[account.id] = now
                    clear_usage_refresh_auth_cooldown(account.id)
            except Exception as exc:
                logger.warning(
                    "Usage refresh failed account_id=%s request_id=%s error=%s",
                    account.id,
                    get_request_id(),
                    exc,
                    exc_info=True,
                )

                continue
            finally:
                if not is_subscription_usable(account):
                    usage_refresh_auth_cooldowns[account.id] = max(
                        usage_refresh_auth_cooldowns.get(account.id, 0.0),
                        time.monotonic() + settings.account_pulse_recovery_interval_seconds,
                    )
        return refreshed

    async def force_refresh(self, account: Account) -> bool:

        settings = get_settings()
        if not settings.usage_refresh_enabled:
            return False
        if account.status in (AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED):
            return False
        if usage_refresh_rate_limit_strikes.get(account.id) and is_usage_refresh_in_cooldown(account.id):
            return False
        try:
            result = await USAGE_REFRESH_SINGLEFLIGHT.run(
                account.id,
                lambda: self._refresh_account(
                    account,
                    usage_account_id=account.chatgpt_account_id,
                ),
                join_existing=False,
            )
            await self._sync_account_from_repo(account)
            if result.fetch_succeeded:
                last_successful_refresh[account.id] = utcnow()
                clear_usage_refresh_auth_cooldown(account.id)
            return result.usage_written
        except Exception as exc:
            logger.warning(
                "Forced usage refresh failed account_id=%s request_id=%s error=%s",
                account.id,
                get_request_id(),
                exc,
                exc_info=True,
            )
            return False

    async def _refresh_account_if_stale(
        self,
        account: Account,
        *,
        usage_account_id: str | None,
        interval_seconds: int,
    ) -> AccountRefreshResult:
        primary_latest = await self._usage_repo.latest_entry_for_account(account.id, window="primary")
        latest = await self._freshness_usage_entry(account, primary_latest)
        if not quota_recovery_should_bypass_freshness(account, latest=latest) and latest_usage_is_fresh(
            latest,
            now=utcnow(),
            interval_seconds=interval_seconds,
        ):
            return AccountRefreshResult(usage_written=False)
        return await self._refresh_account(
            account,
            usage_account_id=usage_account_id,
        )
