from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, AdditionalUsageHistory, UsageHistory
from app.usage_export_headroom import FABLE_QUOTA_KEY, LOOKBACK_HOURS, blocker, exhausted

SCHEMA = "agent-lb-solo-headroom/1"
WINDOWS = {"five_hour": "primary", "weekly": "secondary"}


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _epoch(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _used(row: Any) -> float | None:
    return None if row is None else round(min(100.0, max(0.0, float(row.used_percent))), 2)


def _remaining(used: float | None) -> float | None:
    return None if used is None else round(100.0 - used, 2)


async def _latest_window(session: AsyncSession, account_id: str, window: str, since: datetime) -> UsageHistory | None:
    query = (
        select(UsageHistory)
        .where(UsageHistory.account_id == account_id, UsageHistory.window == window, UsageHistory.recorded_at >= since)
        .order_by(UsageHistory.recorded_at.desc())
        .limit(1)
    )
    return (await session.execute(query)).scalars().first()


async def _latest_fable(session: AsyncSession, account_id: str, since: datetime) -> AdditionalUsageHistory | None:
    query = (
        select(AdditionalUsageHistory)
        .where(
            AdditionalUsageHistory.account_id == account_id,
            AdditionalUsageHistory.quota_key == FABLE_QUOTA_KEY,
            AdditionalUsageHistory.recorded_at >= since,
        )
        .order_by(AdditionalUsageHistory.recorded_at.desc())
        .limit(1)
    )
    return (await session.execute(query)).scalars().first()


def _state(account: Account) -> dict[str, Any]:
    status = account.status.value if hasattr(account.status, "value") else str(account.status)
    return {
        "status": status,
        "subscription_status": account.subscription_status,
        "deactivation_reason": account.deactivation_reason,
        "reset_at": account.reset_at,
        "owner_instance": account.owner_instance,
        "access_expires_at": account.access_expires_at,
    }


def _window_dict(row: Any) -> dict[str, Any] | None:
    return None if row is None else {"used_percent": row.used_percent}


async def _plan(session: AsyncSession, account: Account, now: datetime) -> dict[str, Any]:
    since = now - timedelta(hours=LOOKBACK_HOURS)
    five_hour = await _latest_window(session, account.id, WINDOWS["five_hour"], since)
    weekly = await _latest_window(session, account.id, WINDOWS["weekly"], since)
    fable = await _latest_fable(session, account.id, since)
    state = _state(account)
    reason = blocker(state, now) or exhausted(_window_dict(five_hour), _window_dict(weekly))
    recorded = [row.recorded_at for row in (five_hour, weekly, fable) if row is not None]
    five_used, weekly_used, fable_used = _used(five_hour), _used(weekly), _used(fable)
    return {
        "account": account.id[:8],
        "provider": account.provider,
        "plan_type": account.plan_type,
        "status": state["status"],
        "five_hour_used_pct": five_used,
        "five_hour_remaining_pct": _remaining(five_used),
        "five_hour_resets": _epoch(five_hour.reset_at) if five_hour else None,
        "weekly_used_pct": weekly_used,
        "weekly_remaining_pct": _remaining(weekly_used),
        "weekly_resets": _epoch(weekly.reset_at) if weekly else None,
        "fable_weekly_used_pct": fable_used,
        "fable_weekly_headroom_pct": _remaining(fable_used),
        "limits_recorded_at": _iso(min(recorded)) if recorded else None,
        "usable_now": reason is None,
        "limit_reason": reason,
    }


async def build(session: AsyncSession, instance: str, now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc).replace(tzinfo=None)
    accounts = (await session.execute(select(Account).order_by(Account.created_at, Account.id))).scalars().all()
    plans = [await _plan(session, account, moment) for account in accounts]
    return {
        "schema": SCHEMA,
        "instance": instance,
        "generated_at": _iso(moment),
        "lookback_hours": LOOKBACK_HOURS,
        "plans": plans,
        "usable_now": [plan["account"] for plan in plans if plan["usable_now"]],
    }
