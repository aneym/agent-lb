from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import Integer, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit_quota import quota_allocation
from app.db.models import UsageHistory
from app.db.request_models import RequestLog
from app.modules.accounts.schemas import AccountSummary
from app.modules.pools.pace_curve import (
    OUTPUT_POINTS_PER_MILLION,
    PLAN_FACTORS,
    SINGLE_WINDOW_PROVIDERS,
    account_input,
    excluded,
    pool_pace,
    recent_burn,
    short_window_capacity,
    short_window_ratio,
    sustainable_share,
    timestamp,
)
from app.modules.pools.seat_pace import MIN_CONVERSION_OUTPUT, seat_pools_pace

PROVIDERS = ("anthropic", "openai", "kimi")
FAST_HOURS = 2
SAMPLE_SECONDS = 300
CACHE_SECONDS = 60
_PREVIOUS: dict[str, dict] = {}
_LATEST: dict[str, object] = {}


def weekly_row(provider: str, row) -> bool:
    if provider == "anthropic":
        return row.window == "secondary"
    if provider == "kimi":
        return row.window == "monthly"
    return row.window_minutes == 10080


def short_row(provider: str, row) -> bool:
    return provider == "anthropic" and row.window == "primary" and row.window_minutes == 300


async def sampled_usage(session: AsyncSession, accounts: list[str], since: datetime) -> list:
    bind = session.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        bucket = func.floor(func.extract("epoch", UsageHistory.recorded_at) / SAMPLE_SECONDS)
    else:
        bucket = cast(cast(func.strftime("%s", UsageHistory.recorded_at), Integer) / SAMPLE_SECONDS, Integer)
    windows = or_(
        UsageHistory.window.in_(("secondary", "monthly")),
        UsageHistory.window_minutes.in_((300, 10080)),
    )
    ranked = (
        select(
            UsageHistory.account_id,
            UsageHistory.window,
            UsageHistory.window_minutes,
            UsageHistory.used_percent,
            UsageHistory.recorded_at,
            UsageHistory.reset_at,
            func.row_number()
            .over(
                partition_by=(UsageHistory.account_id, UsageHistory.window, UsageHistory.window_minutes, bucket),
                order_by=UsageHistory.recorded_at.desc(),
            )
            .label("rank"),
        )
        .where(
            and_(UsageHistory.account_id.in_(accounts), UsageHistory.recorded_at >= since.replace(tzinfo=None), windows)
        )
        .subquery()
    )
    statement = select(ranked).where(ranked.c.rank == 1).order_by(ranked.c.recorded_at)
    return list((await session.execute(statement)).all())


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


async def output_since(session: AsyncSession, provider: str, since: datetime) -> int:
    statement = select(func.coalesce(func.sum(RequestLog.output_tokens), 0)).where(
        RequestLog.provider == provider,
        RequestLog.requested_at >= since.replace(tzinfo=None),
        RequestLog.deleted_at.is_(None),
    )
    return int((await session.execute(statement)).scalar_one() or 0)


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
    rows = await sampled_usage(session, [s.account_id for s in members], now - timedelta(hours=25))
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
    slow = recent_burn(weekly, now, factors, 24)
    if conversion is None and provider not in OUTPUT_POINTS_PER_MILLION and slow:
        daily_output = await output_since(session, provider, now - timedelta(hours=24))
        if daily_output >= MIN_CONVERSION_OUTPUT:
            conversion = slow * 24 / (daily_output / 1e6)
    unknown_plans = sorted({s.plan_type for s in members if s.plan_type not in PLAN_FACTORS.get(provider, {})})
    pace = pool_pace(
        accounts,
        now,
        fast=recent_burn(weekly, now, factors, FAST_HOURS),
        slow=slow,
        ungoverned=ungoverned,
        capacity=short_window_capacity(accounts, ratio),
        incomplete=len(accounts) != len(members),
        last_eligible=0 if provider in SINGLE_WINDOW_PROVIDERS else 1,
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
    if conversion is None and provider not in OUTPUT_POINTS_PER_MILLION:
        missing.append("output_conversion")
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
    cached = _LATEST.get("pace")
    if isinstance(cached, dict) and 0 <= (now - cached["at"]).total_seconds() < CACHE_SECONDS:
        return cached["body"]
    body = await compute_pace(summaries, session, now)
    _LATEST["pace"] = {"at": now, "body": body}
    return body


async def compute_pace(summaries: list[AccountSummary], session: AsyncSession, now: datetime) -> dict:
    receipts = await recent_receipts(session, now - timedelta(hours=FAST_HOURS))
    pools, conversions, basis, excluded = {}, {}, {}, {}
    for provider in PROVIDERS:
        if not any(s.provider == provider for s in summaries):
            excluded[provider] = "no_accounts"
            continue
        pools[provider], measured = await provider_pace(provider, summaries, receipts, session, now)
        fallback = OUTPUT_POINTS_PER_MILLION.get(provider)
        if measured or fallback:
            conversions[provider] = measured or fallback
            basis[provider] = "measured" if measured else "council_2026_10_26"
    tick = now.replace(minute=now.minute // 5 * 5, second=0, microsecond=0).isoformat()
    seat_pools, seat_excluded = seat_pools_pace(now, _PREVIOUS, tick, FAST_HOURS)
    for name, pace in seat_pools.items():
        _PREVIOUS[name] = pace
        pools[name] = pace
        if pace["output_points_per_million"]:
            conversions[name] = pace["output_points_per_million"]
            basis[name] = "measured_24h"
    excluded.update(seat_excluded)
    unshared = {name: ",".join(pace["missing"]) or "unknown" for name, pace in pools.items() if name not in conversions}
    reasons = {**unshared, **excluded}
    return {
        "schema_version": 2,
        "shadow": True,
        "enforced": False,
        "generated_at": now.isoformat(),
        "pools": pools,
        "excluded_pools": excluded,
        "s_star": sustainable_share({name: pools[name] for name in conversions}, conversions),
        "s_star_inputs": {
            "pools": list(conversions),
            "points_per_million_output": conversions,
            "basis": basis,
            "excluded": reasons,
        },
        "missing": [f"s_star_excluded:{name}={reason}" for name, reason in reasons.items()],
    }
