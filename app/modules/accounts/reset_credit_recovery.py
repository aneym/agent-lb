from __future__ import annotations

from datetime import datetime

from app.core import usage as usage_core
from app.core.usage.quota import apply_usage_quota
from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import Account, AccountStatus
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.usage.repository import UsageRepository
from app.modules.usage.updater import UsageUpdater


async def refresh_standard_capacity(
    account: Account,
    updater: UsageUpdater,
    usage_repo: UsageRepository,
) -> bool | None:
    started = utcnow()
    if not await updater.force_refresh(account):
        return None
    primary = await usage_repo.latest_entry_for_account(account.id, window="primary")
    secondary = await usage_repo.latest_entry_for_account(account.id, window="secondary")
    monthly = await usage_repo.latest_entry_for_account(account.id, window="monthly")
    if monthly is not None and usage_core.capacity_for_plan(account.plan_type, "monthly") is not None:
        secondary = monthly
    if primary is not None and usage_core.should_use_weekly_primary(primary, secondary):
        secondary, primary = primary, None
    if usage_core.capacity_for_plan(account.plan_type, "primary") == 0:
        primary = None
    if secondary is None:
        return None
    if any(to_utc_naive(row.recorded_at) < started for row in (primary, secondary) if row is not None):
        return None
    credits = next(
        (
            row
            for row in (primary, secondary)
            if row is not None
            and any(value is not None for value in (row.credits_has, row.credits_unlimited, row.credits_balance))
        ),
        secondary,
    )
    status, _, _ = apply_usage_quota(
        status=AccountStatus.ACTIVE,
        primary_used=primary.used_percent if primary else None,
        primary_reset=primary.reset_at if primary else None,
        primary_window_minutes=primary.window_minutes if primary else None,
        runtime_reset=None,
        secondary_used=secondary.used_percent,
        secondary_reset=secondary.reset_at,
        credits_has=credits.credits_has,
        credits_unlimited=credits.credits_unlimited,
        credits_balance=credits.credits_balance,
    )
    return status == AccountStatus.ACTIVE


async def capacity_recorded_since(account_id: str, usage_repo: UsageRepository, since: datetime) -> bool:
    cutoff = to_utc_naive(since)
    readings = [
        await usage_repo.latest_entry_for_account(account_id, window=window) for window in ("primary", "secondary")
    ]
    secondary = readings[1]
    if secondary is None or to_utc_naive(secondary.recorded_at) <= cutoff:
        return False
    return all(
        row.used_percent < 100.0 for row in readings if row is not None and to_utc_naive(row.recorded_at) > cutoff
    )


async def clear_recovered_reset_cooldown(account_id: str, repo: AccountsRepository) -> None:
    current = await repo.reload_by_id(account_id)
    if current is None or current.status not in (
        AccountStatus.ACTIVE,
        AccountStatus.RATE_LIMITED,
        AccountStatus.QUOTA_EXCEEDED,
    ):
        return
    await repo.update_status_if_current(
        account_id,
        AccountStatus.ACTIVE,
        None,
        None,
        blocked_at=None,
        expected_status=current.status,
        expected_deactivation_reason=current.deactivation_reason,
        expected_reset_at=current.reset_at,
        expected_blocked_at=current.blocked_at,
    )
    get_account_selection_cache().invalidate()
