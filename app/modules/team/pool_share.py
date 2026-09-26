"""Estimate pool share from this instance's request logs only.

Usage snapshots report global account consumption, but request logs are local.
ONLY while all of a member's traffic lands on this instance, the local estimate
is an upper bound on the share computed with global logs (the safe direction).
With mixed priced and unpriced rows, remote priced traffic can change the
imputation rate, so the bound uses the larger of the local priced-cost fraction
and the local unpriced-token fraction instead of a local blended rate. Members
must use a single LB URL without fallback until federation usage reports feed
both the numerator and denominator. A fallback can put member traffic on
another instance and understate the true share.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import DateTime, Integer, String, and_, case, column, func, select, values
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, AccountStatus, ApiKey, ApiKeyAccountAssignment, RequestLog, UsageHistory

_CACHE_SECONDS = 5.0
_CACHE_SIZE = 1024


@dataclass(frozen=True, slots=True)
class PoolWindow:
    window: str
    used_percent: float
    limit_percent: float
    reset_at: datetime


@dataclass(frozen=True, slots=True)
class _Snapshot:
    account_id: str
    length: int
    start: datetime
    reset_at: datetime
    used_percent: float
    current: bool


@dataclass(frozen=True, slots=True)
class _Totals:
    priced_cost: float
    priced_rows: int
    priced_tokens: int
    unpriced_tokens: int


_members: dict[tuple[str, float], tuple[float, list[PoolWindow]]] = {}
_cache_generation = 0


def reset_pool_share_cache() -> None:
    global _cache_generation
    _cache_generation += 1
    _members.clear()


def _cache_put(key: tuple[str, float], value: tuple[float, list[PoolWindow]], now: float) -> None:
    # Expired entries do not need to consume the bounded cache indefinitely.
    for expired in [entry for entry, (deadline, _) in _members.items() if deadline <= now]:
        _members.pop(expired, None)
    if len(_members) >= _CACHE_SIZE:
        _members.pop(next(iter(_members)))
    _members[key] = value


def _window_label(length: int) -> str:
    return {300: "pool_5h", 10080: "pool_week"}.get(length, f"pool_{length}m")


def _totals(row: tuple[object, ...] | None) -> _Totals:
    if row is None:
        return _Totals(0.0, 0, 0, 0)
    return _Totals(float(row[2] or 0), int(row[3] or 0), int(row[4] or 0), int(row[5] or 0))


def _local_upper_fraction(member: _Totals, account: _Totals) -> float:
    # A remote priced row can change the global imputation rate arbitrarily.
    # The global blended fraction is a weighted average of these two local
    # fractions (with nonnegative remote traffic added only to its denominator).
    # Thus their maximum remains an upper bound when the member is local.
    if account.priced_rows == 0:
        total = account.priced_tokens + account.unpriced_tokens
        return (member.priced_tokens + member.unpriced_tokens) / total if total else 0.0
    priced = member.priced_cost / account.priced_cost if account.priced_cost > 0 else 0.0
    unpriced = member.unpriced_tokens / account.unpriced_tokens if account.unpriced_tokens > 0 else 0.0
    return max(priced, unpriced)


async def _reachable_accounts(session: AsyncSession, member_id: str) -> set[str]:
    keys = (
        await session.execute(
            select(ApiKey.id, ApiKey.account_assignment_scope_enabled).where(
                ApiKey.member_id == member_id, ApiKey.is_active.is_(True)
            )
        )
    ).all()
    if all(scoped for _, scoped in keys):
        assigned = select(ApiKeyAccountAssignment.account_id).where(
            ApiKeyAccountAssignment.api_key_id.in_([key_id for key_id, _ in keys])
        )
        query = select(Account.id).where(Account.id.in_(assigned))
    else:
        query = select(Account.id)
    query = query.where(Account.status.notin_((AccountStatus.DEACTIVATED, AccountStatus.REAUTH_REQUIRED)))
    return set((await session.execute(query)).scalars().all())


async def _snapshots(session: AsyncSession, account_ids: set[str], now: datetime) -> list[_Snapshot]:
    if not account_ids:
        return []
    snapshots: dict[tuple[str, int], _Snapshot] = {}
    for window in ("primary", "secondary"):
        latest_id = (
            select(UsageHistory.id)
            .where(
                UsageHistory.account_id == Account.id,
                func.coalesce(UsageHistory.window, "primary") == window,
            )
            .order_by(UsageHistory.recorded_at.desc(), UsageHistory.id.desc())
            .limit(1)
            .correlate(Account)
            .scalar_subquery()
        )
        rows = (
            (
                await session.execute(
                    select(UsageHistory)
                    .join(Account, UsageHistory.account_id == Account.id)
                    .where(Account.id.in_(account_ids), UsageHistory.id == latest_id)
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            length = row.window_minutes
            reset = row.reset_at
            if length is None or length <= 0 or reset is None:
                continue
            reset_at = datetime.fromtimestamp(reset, tz=timezone.utc).replace(tzinfo=None)
            start = reset_at - timedelta(minutes=length)
            current = reset_at > now and row.recorded_at.replace(tzinfo=None) >= start
            # A rolled window remains eligible at zero even if the last sample is old.
            rolled = reset_at <= now
            if not current and not rolled:
                continue
            snapshot = _Snapshot(row.account_id, length, start, reset_at, row.used_percent, current)
            key = (row.account_id, length)
            previous = snapshots.get(key)
            if previous is None or (snapshot.reset_at, snapshot.current) > (previous.reset_at, previous.current):
                snapshots[key] = snapshot
    return list(snapshots.values())


async def _aggregate(
    session: AsyncSession, snapshots: list[_Snapshot], *, member_id: str | None = None
) -> dict[tuple[str, int], _Totals]:
    if not snapshots:
        return {}
    # VALUES in a CTE avoids SQLite's 500-term limit for compound SELECTs.
    windows = (
        values(column("account_id", String), column("length", Integer), column("start", DateTime))
        .data([(snapshot.account_id, snapshot.length, snapshot.start) for snapshot in snapshots])
        .cte("pool_windows")
    )
    tokens = func.coalesce(RequestLog.input_tokens, 0) + func.coalesce(RequestLog.output_tokens, 0)
    stmt = (
        select(
            windows.c.account_id,
            windows.c.length,
            func.coalesce(func.sum(case((RequestLog.cost_usd.is_not(None), RequestLog.cost_usd), else_=0)), 0),
            func.coalesce(func.sum(case((RequestLog.cost_usd.is_not(None), 1), else_=0)), 0),
            func.coalesce(func.sum(case((RequestLog.cost_usd.is_not(None), tokens), else_=0)), 0),
            func.coalesce(func.sum(case((RequestLog.cost_usd.is_(None), tokens), else_=0)), 0),
        )
        .select_from(windows)
        .join(
            RequestLog,
            and_(RequestLog.account_id == windows.c.account_id, RequestLog.requested_at >= windows.c.start),
        )
        # Sargable bounds so the planner can use the requested_at / account_id
        # indexes instead of scanning all of request_logs on the admission path.
        .where(
            RequestLog.account_id.in_({snapshot.account_id for snapshot in snapshots}),
            RequestLog.requested_at >= min(snapshot.start for snapshot in snapshots),
        )
        .group_by(windows.c.account_id, windows.c.length)
    )
    if member_id is not None:
        stmt = stmt.join(ApiKey, ApiKey.id == RequestLog.api_key_id).where(ApiKey.member_id == member_id)
    rows = (await session.execute(stmt)).all()
    return {(row[0], row[1]): _totals(row) for row in rows}


async def pool_share_windows(session: AsyncSession, member_id: str, limit: float, *, now: datetime) -> list[PoolWindow]:
    clock = time.monotonic()
    generation = _cache_generation
    cache_key = (member_id, limit)
    cached = _members.get(cache_key)
    if cached is not None and cached[0] > clock and all(window.reset_at > now for window in cached[1]):
        return cached[1]

    accounts = await _reachable_accounts(session, member_id)
    snapshots = await _snapshots(session, accounts, now)
    current = [snapshot for snapshot in snapshots if snapshot.current]
    # Keep both aggregates local to this evaluation: invalidation during an await
    # cannot remove a denominator, and no result can inherit a stale cache TTL.
    denominators = await _aggregate(session, current)
    members = await _aggregate(session, current, member_id=member_id)
    grouped: dict[int, list[tuple[_Snapshot, float]]] = {}
    for snapshot in snapshots:
        attributed = 0.0
        if snapshot.current:
            key = (snapshot.account_id, snapshot.length)
            denominator = denominators.get(key, _Totals(0, 0, 0, 0))
            numerator = members.get(key, _Totals(0, 0, 0, 0))
            fraction = _local_upper_fraction(numerator, denominator)
            attributed = min(max(snapshot.used_percent, 0), 100) * min(max(fraction, 0), 1)
        grouped.setdefault(snapshot.length, []).append((snapshot, attributed))
    result = []
    for length, entries in sorted(grouped.items()):
        reset = min(
            (snapshot.reset_at for snapshot, attributed in entries if attributed > 0),
            default=min(
                (snapshot.reset_at for snapshot, _ in entries if snapshot.current),
                default=now + timedelta(minutes=length),
            ),
        )
        result.append(
            PoolWindow(_window_label(length), sum(value for _, value in entries) / len(entries), limit, reset)
        )
    if generation == _cache_generation:
        _cache_put(cache_key, (clock + _CACHE_SECONDS, result), clock)
    return result
