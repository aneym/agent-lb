from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit_quota import quota_allocation
from app.db.models import UsageHistory
from app.db.request_models import RequestLog
from app.modules.accounts.schemas import AccountSummary
from app.modules.pools.pace_curve import account_input, pool_pace, slow_burn, timestamp

_PREVIOUS: dict[str, dict] = {}


async def build_pace(summaries: list[AccountSummary], session: AsyncSession, now: datetime) -> dict:
    now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    result = {}
    since = now - timedelta(hours=2)
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
    receipts = [
        dict(row)
        for row in (
            await session.execute(
                select(*columns).where(RequestLog.requested_at >= since, RequestLog.deleted_at.is_(None))
            )
        ).mappings()
    ]
    for receipt in receipts:
        receipt["requested_at"] = timestamp(receipt["requested_at"])
    for provider in ("anthropic", "openai"):
        members = [a for a in summaries if a.provider == provider]
        accounts = [a for summary in members if (a := account_input(summary)) is not None]
        statement = (
            select(UsageHistory)
            .where(
                UsageHistory.account_id.in_([a.account_id for a in members]),
                UsageHistory.recorded_at >= now - timedelta(hours=25),
            )
            .order_by(UsageHistory.recorded_at)
        )
        rows = (await session.execute(statement)).scalars().all()
        history = [
            {
                "account": row.account_id,
                "at": timestamp(row.recorded_at),
                "remaining": 100 - row.used_percent,
                "reset": row.reset_at,
            }
            for row in rows
            if (provider == "anthropic" and row.window == "secondary")
            or (provider == "openai" and row.window == "primary" and row.window_minutes == 10080)
        ]
        slow = slow_burn(history, now)
        snapshots = [
            {
                "account_id": row.account_id,
                "provider": row.provider,
                "window": row.window,
                "window_minutes": row.window_minutes,
                "used_percent": row.used_percent,
                "recorded_at": timestamp(row.recorded_at),
                "reset_at": row.reset_at,
            }
            for row in rows
        ]
        provider_receipts = [r for r in receipts if r["provider"] == provider]
        allocated, audit = quota_allocation(provider_receipts, snapshots, since, now)
        measured = audit["providers"].get(provider + ":weekly")
        fast = measured["quota_points"] / 2 if measured else None
        unlinked = sum(p["weekly"] for r, p in zip(provider_receipts, allocated, strict=True) if not r["job_id"])
        unlinked += sum(r["points"] for r in audit["unattributed"] if r["window"] == "weekly")
        pace = pool_pace(
            accounts,
            now,
            fast=fast,
            slow=slow,
            ungoverned=unlinked / 2 if measured else None,
            previous=_PREVIOUS.get(provider),
            tick=now.replace(minute=now.minute // 5 * 5, second=0, microsecond=0).isoformat(),
        )
        pace["missing"] = ["plan_factors", "short_window_capacity"]
        if len(accounts) != len(members):
            pace["stale"] = True
            pace["missing"].append("account_quota")
        _PREVIOUS[provider] = pace
        result[provider] = pace
    return {
        "schema_version": 1,
        "shadow": True,
        "generated_at": now.isoformat(),
        "pools": result,
        "s_star": None,
        "missing": ["single_window_concurrency_and_measured_throughput"],
    }
