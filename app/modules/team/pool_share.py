"""Estimate pool share from this instance's request logs only.

Usage snapshots report global account consumption, but request logs are local.
ONLY while all of a member's traffic lands on this instance, remote traffic
increases the true account denominator and the local estimate overstates share
(the safe direction). Members must use a single LB URL without fallback until
federation usage reports feed both the numerator and denominator. A fallback
can put member traffic on another instance and understate the true share.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, func, literal, select, union_all
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


_denominators: dict[tuple[str, int, datetime], tuple[float, _Totals]] = {}
_members: dict[tuple[str, float], tuple[float, list[PoolWindow]]] = {}


def reset_pool_share_cache() -> None:
    _denominators.clear()
    _members.clear()


def _cache_put(cache: dict, key: object, value: object) -> None:
    if len(cache) >= _CACHE_SIZE:
        cache.clear()
    cache[key] = value


def _window_label(length: int) -> str:
    return {300: "pool_5h", 10080: "pool_week"}.get(length, f"pool_{length}m")


def _totals(row: tuple[object, ...] | None) -> _Totals:
    if row is None:
        return _Totals(0.0, 0, 0, 0)
    return _Totals(float(row[2] or 0), int(row[3] or 0), int(row[4] or 0), int(row[5] or 0))


def _effective_cost(member: _Totals, account: _Totals) -> tuple[float, float]:
    if account.priced_rows > 0 and account.priced_tokens > 0:
        rate = account.priced_cost / account.priced_tokens
        return (
            member.priced_cost + member.unpriced_tokens * rate,
            account.priced_cost + account.unpriced_tokens * rate,
        )
    if account.priced_rows > 0:
        return member.priced_cost, account.priced_cost
    return (
        float(member.priced_tokens + member.unpriced_tokens),
        float(account.priced_tokens + account.unpriced_tokens),
    )


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
            # A previous-window sample can roll once; obsolete legacy rows cannot
            # manufacture a window years after that quota disappeared.
            rolled = reset_at <= now and reset_at + timedelta(minutes=length) > now
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
    window_queries = [
        select(
            literal(snapshot.account_id).label("account_id"),
            literal(snapshot.length).label("length"),
            literal(snapshot.start).label("start"),
        )
        for snapshot in snapshots
    ]
    windows = union_all(*window_queries).subquery() if len(window_queries) > 1 else window_queries[0].subquery()
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
        .group_by(windows.c.account_id, windows.c.length)
    )
    if member_id is not None:
        stmt = stmt.join(ApiKey, ApiKey.id == RequestLog.api_key_id).where(ApiKey.member_id == member_id)
    rows = (await session.execute(stmt)).all()
    return {(row[0], row[1]): _totals(row) for row in rows}


async def pool_share_windows(session: AsyncSession, member_id: str, limit: float, *, now: datetime) -> list[PoolWindow]:
    clock = time.monotonic()
    cache_key = (member_id, limit)
    cached = _members.get(cache_key)
    if cached is not None and cached[0] > clock and all(window.reset_at > now for window in cached[1]):
        return cached[1]

    accounts = await _reachable_accounts(session, member_id)
    snapshots = await _snapshots(session, accounts, now)
    current = [snapshot for snapshot in snapshots if snapshot.current]
    missing = [
        snapshot
        for snapshot in current
        if (entry := _denominators.get((snapshot.account_id, snapshot.length, snapshot.start))) is None
        or entry[0] <= clock
    ]
    fresh = await _aggregate(session, missing)
    for snapshot in missing:
        key = (snapshot.account_id, snapshot.length, snapshot.start)
        _cache_put(
            _denominators,
            key,
            (clock + _CACHE_SECONDS, fresh.get((snapshot.account_id, snapshot.length), _Totals(0, 0, 0, 0))),
        )
    members = await _aggregate(session, current, member_id=member_id)
    grouped: dict[int, list[tuple[_Snapshot, float]]] = {}
    for snapshot in snapshots:
        attributed = 0.0
        if snapshot.current:
            denominator = _denominators[(snapshot.account_id, snapshot.length, snapshot.start)][1]
            numerator = members.get((snapshot.account_id, snapshot.length), _Totals(0, 0, 0, 0))
            member_cost, total_cost = _effective_cost(numerator, denominator)
            if total_cost > 0:
                attributed = min(max(snapshot.used_percent, 0), 100) * member_cost / total_cost
        grouped.setdefault(snapshot.length, []).append((snapshot, attributed))
    result = []
    for length, entries in sorted(grouped.items()):
        reset = min(
            (snapshot.reset_at for snapshot, attributed in entries if attributed > 0),
            default=min(snapshot.reset_at for snapshot, _ in entries),
        )
        result.append(
            PoolWindow(_window_label(length), sum(value for _, value in entries) / len(entries), limit, reset)
        )
    _cache_put(_members, cache_key, (clock + _CACHE_SECONDS, result))
    return result
