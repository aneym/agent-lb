from __future__ import annotations

from typing import Any, cast

from app.core.plan_types import normalize_account_plan_type
from app.core.providers import ANTHROPIC_PROVIDER_NAME
from app.core.usage.refresh_recovery import (
    _background_accounts_repo,
    _BackgroundLimitWarmupRepository,
    _BackgroundRequestLogsRepository,
    _invalidate_usage_refresh_caches,
    _ordered_usage_refresh_accounts,
    _resolve_monthly_reset_evidence,
    _select_long_window_entries,
    _usage_refresh_slice_seconds,
    logger,
    reconcile_recoverable_account_statuses,
)
from app.db.session import detach_session_objects, get_background_session
from app.modules.accounts.background_repository import BackgroundAccountsRepository
from app.modules.accounts.repository import AccountsRepository
from app.modules.limit_warmup.service import LimitWarmupService, StreamingLimitWarmupSender
from app.modules.settings.repository import SettingsRepository
from app.modules.usage import updater as usage_updater_module
from app.modules.usage.repository import UsageRepository
from app.modules.usage.updater import build_background_usage_updater


class UsageLeaderRefresh:
    async def _refresh_as_leader(self) -> float:
        async with self._lock:
            account_count = 0
            try:
                async with get_background_session() as session:
                    usage_repo = UsageRepository(session)
                    accounts_repo = AccountsRepository(session)
                    accounts = _ordered_usage_refresh_accounts(
                        [a for a in await accounts_repo.list_accounts() if a.provider != ANTHROPIC_PROVIDER_NAME]
                    )
                    selected_account, cycle_complete = self._select_next_account(accounts)
                    if selected_account is not None:
                        selected_account_ids = [selected_account.id]
                        previous_plan_types = {
                            selected_account.id: normalize_account_plan_type(selected_account.plan_type)
                        }
                        before_primary = await usage_repo.latest_by_account(
                            window="primary", account_ids=selected_account_ids
                        )
                        before_secondary = await usage_repo.latest_by_account(
                            window="secondary", account_ids=selected_account_ids
                        )
                        before_monthly = await usage_repo.latest_by_account(
                            window="monthly", account_ids=selected_account_ids
                        )
                    detach_session_objects(session)
                account_count = len(accounts)
                if selected_account is None:
                    await _invalidate_usage_refresh_caches()
                    return float(self.interval_seconds)
                updater = build_background_usage_updater()
                refresh_started_at = usage_updater_module.utcnow()
                usage_written = await updater.refresh_accounts([selected_account], before_primary)
                if usage_written:
                    async with get_background_session() as session:
                        usage_repo = UsageRepository(session)
                        accounts_repo = AccountsRepository(session)
                        settings_repo = SettingsRepository(session)
                        after_primary = await usage_repo.latest_by_account(
                            window="primary", account_ids=selected_account_ids
                        )
                        after_secondary = await usage_repo.latest_by_account(
                            window="secondary", account_ids=selected_account_ids
                        )
                        after_monthly = await usage_repo.latest_by_account(
                            window="monthly", account_ids=selected_account_ids
                        )
                        dashboard_settings = await settings_repo.get_or_create()
                        refreshed_accounts = await accounts_repo.list_accounts(refresh_existing=True)
                        refreshed_selected_accounts = [
                            account for account in refreshed_accounts if account.id == selected_account.id
                        ]
                        monthly_reset_evidence = await _resolve_monthly_reset_evidence(
                            accounts=refreshed_selected_accounts,
                            usage_repo=usage_repo,
                            before_monthly=before_monthly,
                            after_monthly=after_monthly,
                        )
                        detach_session_objects(session)
                    warmup_before_monthly = dict(before_monthly)
                    warmup_after_monthly = dict(after_monthly)
                    for account_id, reset_evidence in monthly_reset_evidence.items():
                        warmup_before_monthly[account_id] = reset_evidence.before
                        warmup_after_monthly[account_id] = reset_evidence.after
                    async with get_background_session() as session:
                        await reconcile_recoverable_account_statuses(
                            accounts_repo=AccountsRepository(session),
                            usage_repo=UsageRepository(session),
                            accounts=refreshed_selected_accounts,
                            monthly_reset_evidence=monthly_reset_evidence,
                        )
                    warmup_service = LimitWarmupService(
                        cast(Any, _BackgroundLimitWarmupRepository()),
                        cast(Any, _BackgroundRequestLogsRepository()),
                        sender=StreamingLimitWarmupSender(
                            cast(AccountsRepository, BackgroundAccountsRepository()),
                            accounts_repo_factory=_background_accounts_repo,
                        ),
                    )
                    await warmup_service.run_after_usage_refresh(
                        accounts=refreshed_selected_accounts,
                        stagger_accounts=refreshed_accounts,
                        settings=dashboard_settings,
                        before_primary=before_primary,
                        before_secondary=_select_long_window_entries(
                            accounts=refreshed_selected_accounts,
                            monthly_entries=warmup_before_monthly,
                            secondary_entries=before_secondary,
                        ),
                        after_primary=after_primary,
                        after_secondary=_select_long_window_entries(
                            accounts=refreshed_selected_accounts,
                            monthly_entries=warmup_after_monthly,
                            secondary_entries=after_secondary,
                        ),
                        previous_plan_types=previous_plan_types,
                        refresh_started_at=refresh_started_at,
                        usage_refresh_interval_seconds=self.interval_seconds,
                    )
                if cycle_complete:
                    await _invalidate_usage_refresh_caches()
            except Exception:
                logger.exception("Usage refresh loop failed")
                return float(self.interval_seconds)
        return _usage_refresh_slice_seconds(self.interval_seconds, account_count)
