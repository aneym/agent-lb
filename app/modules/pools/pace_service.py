from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit_quota import quota_allocation
from app.db.models import UsageHistory
from app.db.request_models import RequestLog
from app.modules.accounts.schemas import AccountSummary
from app.modules.pools.pace_curve import (
    OUTPUT_POINTS_PER_MILLION,
    PLAN_FACTORS,
    account_input,
    excluded,
    pool_pace,
    recent_burn,
    short_window_capacity,
    short_window_ratio,
    sustainable_share,
    timestamp,
)

PROVIDERS = ("anthropic", "openai")
FAST_HOURS = 2
_PREVIOUS: dict[str, dict] = {}


def weekly_row(provider: str, row: UsageHistory) -> bool:
    if provider == "anthropic":
        return row.window == "secondary"
    return row.window_minutes == 10080


def short_row(provider: str, row: UsageHistory) -> bool:
    return provider == "anthropic" and row.window == "primary" and row.window_minutes == 300


async def recent_receipts(session: AsyncSession, since: datetime) -> list[dict]:
    columns = [
        RequestLog.account_id,
        RequestLog.provider,
        RequestLog.model,
        RequestLog.requested_at,
        RequestLog.status,
        RequestLog.job_id,
        RequestLog.cost_usd,
        RequestLog.input_tokens,
        RequestLog.output_tokens,
        RequestLog.cached_input_tokens,
        RequestLog.cache_read_tokens,
        RequestLog.cache_creation_tokens,
        RequestLog.reasoning_tokens,
    ]
    statement = select(*columns).where(
        RequestLog.requested_at >= since.replace(tzinfo=None), RequestLog.deleted_at.is_(None)
    )
    receipts = [dict(row) for row in (await session.execute(statement)).mappings()]
    for receipt in receipts:
        receipt["requested_at"] = timestamp(receipt["requested_at"])
    return receipts


async def provider_pace(
    provider: str,
    summaries: list[AccountSummary],
    receipts: list[dict],
    session: AsyncSession,
    now: datetime,
) -> tuple[dict, float | None]:
    members = [s for s in summaries if s.provider == provider and not excluded(s)]
    inputs = {s.account_id: account_input(s) for s in members}
    accounts = [a for a in inputs.values() if a is not None]
    factors = {a.id: a.factor for a in accounts}
    statement = (
        select(UsageHistory)
        .where(
            UsageHistory.account_id.in_([s.account_id for s in members]),
            UsageHistory.recorded_at >= (now - timedelta(hours=25)).replace(tzinfo=None),
        )
        .order_by(UsageHistory.recorded_at)
    )
    rows = (await session.execute(statement)).scalars().all()
    weekly = [
        {
            "account": r.account_id,
            "at": timestamp(r.recorded_at),
            "remaining": 100 - r.used_percent,
            "reset": r.reset_at,
        }
        for r in rows
        if weekly_row(provider, r)
    ]
    windows = [
        {
            "account": r.account_id,
            "at": timestamp(r.recorded_at),
            "used": r.used_percent,
            "reset": r.reset_at,
            "window": "weekly" if weekly_row(provider, r) else "5h",
        }
        for r in rows
        if weekly_row(provider, r) or short_row(provider, r)
    ]
    ratio = short_window_ratio(windows) if provider == "anthropic" else None
    since = now - timedelta(hours=FAST_HOURS)
    provider_receipts = [r for r in receipts if r["provider"] == provider]
    snapshots = [
        {
            "account_id": r.account_id,
            "provider": provider,
            "window": r.window,
            "window_minutes": r.window_minutes,
            "used_percent": r.used_percent,
            "recorded_at": timestamp(r.recorded_at),
            "reset_at": r.reset_at,
        }
        for r in rows
    ]
    allocated, audit = quota_allocation(provider_receipts, snapshots, since, now)
    measured = audit["providers"].get(provider + ":weekly")
    ungoverned = None
    if measured:
        unlinked = sum(p["weekly"] for r, p in zip(provider_receipts, allocated, strict=True) if not r["job_id"])
        unlinked += sum(r["points"] for r in audit["unattributed"] if r["window"] == "weekly")
        ungoverned = unlinked / FAST_HOURS
    output = sum(r["output_tokens"] or 0 for r in provider_receipts if r.get("status", "success") == "success")
    conversion = measured["quota_points"] / (output / 1e6) if measured and output >= 1e6 else None
    unknown_plans = sorted({s.plan_type for s in members if s.plan_type not in PLAN_FACTORS.get(provider, {})})
    pace = pool_pace(
        accounts,
        now,
        fast=recent_burn(weekly, now, factors, FAST_HOURS),
        slow=recent_burn(weekly, now, factors, 24),
        ungoverned=ungoverned,
        capacity=short_window_capacity(accounts, ratio),
        incomplete=len(accounts) != len(members),
        previous=_PREVIOUS.get(provider),
        tick=now.replace(minute=now.minute // 5 * 5, second=0, microsecond=0).isoformat(),
    )
    missing = []
    if unknown_plans:
        missing.append("plan_factor:" + ",".join(unknown_plans))
    if any(a is None for a in inputs.values()):
        missing.append("account_quota")
    if provider == "anthropic" and ratio is None:
        missing.append("short_window_ratio")
    pace.update(
        plan_factors={a.id: a.factor for a in accounts},
        short_window_ratio=ratio,
        output_points_per_million=conversion,
        missing=missing,
    )
    _PREVIOUS[provider] = pace
    return pace, conversion


async def build_pace(summaries: list[AccountSummary], session: AsyncSession, now: datetime) -> dict:
    now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    receipts = await recent_receipts(session, now - timedelta(hours=FAST_HOURS))
    pools, conversions, basis = {}, {}, {}
    for provider in PROVIDERS:
        pools[provider], measured = await provider_pace(provider, summaries, receipts, session, now)
        conversions[provider] = measured or OUTPUT_POINTS_PER_MILLION[provider]
        basis[provider] = "measured_2h" if measured else "council_2026_10_26"
    return {
        "schema_version": 2,
        "shadow": True,
        "enforced": False,
        "generated_at": now.isoformat(),
        "pools": pools,
        "s_star": sustainable_share(pools, conversions),
        "s_star_inputs": {"pools": list(PROVIDERS), "points_per_million_output": conversions, "basis": basis},
        "missing": ["s_star_single_window_pools:grok,kimi,cursor,devin"],
    }
