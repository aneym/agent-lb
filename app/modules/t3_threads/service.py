from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.time import utcnow
from app.db.t3_models import T3NativeSession, T3Thread
from app.modules.accounts.schemas import AccountSummary
from app.modules.pools.pace_curve import excluded, plan_factor
from app.modules.pools.pace_service import sampled_usage, weekly_row
from app.modules.t3_threads.attribution import HOUR_SECONDS, apportion, hour_index, weekly_rises
from app.modules.t3_threads.repository import POOL_PROVIDERS, RequestGroup, T3ThreadsRepository
from app.modules.t3_threads.schemas import (
    BurnResponse,
    HourPoint,
    NativeSessionRef,
    PoolBurn,
    PoolShare,
    PoolThreadBurn,
    ThreadBurn,
)

RANGES = {"24h": 24, "7d": 168}
CACHE_SECONDS = 60
DELEGATED_PREFIX = "thread:delegated-task:"
OUTSIDE = "outside"
UNREGISTERED = "unregistered"
THREAD = "thread"
CLAUDE_CALLERS = ("claude-cli", "claude")

_CACHE: dict[str, tuple[float, Loaded]] = {}
_LOCK = asyncio.Lock()


def clear_cache() -> None:
    _CACHE.clear()


@dataclass(frozen=True)
class Loaded:
    now: datetime
    start: datetime
    groups: list[RequestGroup]
    rises: dict[tuple[str, int], float]
    accounts: dict[str, str]


@dataclass
class Tally:
    pct: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    requests: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    input_tokens: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    output_tokens: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    cache_read_tokens: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    models: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    hourly: dict[str, dict[int, float]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(float)))
    last_at: datetime | None = None

    def add(self, group: RequestGroup, pct: float, family: str) -> None:
        provider = group.provider
        self.pct[provider] += pct
        self.requests[provider] += group.requests
        self.input_tokens[provider] += group.input_tokens
        self.output_tokens[provider] += group.output_tokens
        self.cache_read_tokens[provider] += group.cache_read_tokens
        self.models[family] += group.requests
        self.hourly[provider][group.hour] += pct
        if self.last_at is None or group.last_at > self.last_at:
            self.last_at = group.last_at


def model_family(group: RequestGroup, driver: str | None) -> str:
    name = group.model.lower()
    if "-sol" in name:
        claude_caller = driver == "claudeAgent" or (group.ua_group or "").lower() in CLAUDE_CALLERS
        return "Sol via wrappers" if claude_caller and driver != "codex" else "Codex"
    if name.startswith("claude-"):
        return name.split("-")[1].capitalize()
    return "Codex"


def label_of(group: RequestGroup, native: dict[str, T3NativeSession]) -> str:
    if group.session in native:
        return THREAD
    if (group.seat or "").startswith("t3-") or group.ua_group == "T3":
        return UNREGISTERED
    return OUTSIDE


def group_weights(groups: list[RequestGroup]) -> list[float]:
    cost: dict[str, float] = defaultdict(float)
    priced: dict[str, float] = defaultdict(float)
    for group in groups:
        cost[group.provider] += group.cost
        priced[group.provider] += group.priced_equivalents
    rate = {
        provider: cost[provider] / priced[provider]
        for provider in priced
        if priced[provider] > 0 and cost[provider] > 0
    }
    return [group.cost + group.unpriced_equivalents * rate.get(group.provider, 1.0) for group in groups]


def pool_accounts(summaries: list[AccountSummary]) -> tuple[dict[str, float], dict[str, str]]:
    factors: dict[str, float] = {}
    providers: dict[str, str] = {}
    for summary in summaries:
        factor = plan_factor(summary.provider, summary.plan_type)
        if summary.provider in POOL_PROVIDERS and factor is not None:
            factors[summary.account_id] = factor
            providers[summary.account_id] = summary.provider
    return factors, providers


def pool_capacity(summaries: list[AccountSummary]) -> dict[str, float]:
    capacity: dict[str, float] = defaultdict(float)
    for summary in summaries:
        factor = plan_factor(summary.provider, summary.plan_type)
        if summary.provider in POOL_PROVIDERS and factor is not None and not excluded(summary):
            capacity[summary.provider] += factor
    return capacity


async def load(session: AsyncSession, summaries: list[AccountSummary], window: str) -> Loaded:
    async with _LOCK:
        cached = _CACHE.get(window)
        if cached is not None and time.monotonic() - cached[0] < CACHE_SECONDS:
            return cached[1]
        now = utcnow()
        start = (now - timedelta(hours=RANGES[window])).replace(minute=0, second=0, microsecond=0)
        factors, providers = pool_accounts(summaries)
        groups = await T3ThreadsRepository(session).request_groups(start)
        samples = await sampled_usage(session, list(factors), start - timedelta(hours=1))
        by_account: dict[str, list] = defaultdict(list)
        for row in samples:
            if weekly_row(providers[row.account_id], row):
                by_account[row.account_id].append((row.recorded_at, row.used_percent, row.reset_at))
        first = hour_index(start)
        rises = {
            (account, hour): rise
            for account, rows in by_account.items()
            for hour, rise in weekly_rises(rows, factors[account]).items()
            if hour >= first
        }
        loaded = Loaded(now, start, groups, rises, providers)
        _CACHE[window] = (time.monotonic(), loaded)
        return loaded


def thread_info(
    host: str,
    thread_id: str,
    threads: dict[tuple[str, str], T3Thread],
    sessions_by_thread: dict[tuple[str, str], list[T3NativeSession]],
) -> ThreadBurn:
    row = threads.get((host, thread_id))
    return ThreadBurn(
        host=host,
        thread_id=thread_id,
        root_thread_id=row.root_thread_id if row else thread_id,
        title=row.title if row else None,
        project_title=row.project_title if row else None,
        created_at=row.created_at if row else None,
        archived=bool(row and row.archived_at),
        deleted=bool(row and row.deleted_at),
        native_sessions=[
            NativeSessionRef(native_id=s.native_id, driver=s.driver)
            for s in sorted(sessions_by_thread.get((host, thread_id), []), key=lambda s: s.native_id)
        ],
    )


def finish(
    info: ThreadBurn,
    tally: Tally,
    capacity: dict[str, float],
    b_star: dict[str, float | None],
    hours: float,
    with_hourly: bool,
) -> ThreadBurn:
    pools = {}
    for provider in POOL_PROVIDERS:
        pct = tally.pct.get(provider, 0.0)
        star = b_star.get(provider)
        pools[provider] = PoolThreadBurn(
            pool_share_pct=pct / (100 * capacity[provider]) * 100 if capacity.get(provider) else 0.0,
            account_week_pct=pct,
            pace_multiple=pct / hours / star if star and star > 0 and pct > 0 else None,
            requests=tally.requests.get(provider, 0),
            input_tokens=tally.input_tokens.get(provider, 0),
            output_tokens=tally.output_tokens.get(provider, 0),
            cache_read_tokens=tally.cache_read_tokens.get(provider, 0),
        )
    info.pools = pools
    info.models = dict(tally.models)
    if tally.last_at is not None:
        info.last_request_at = tally.last_at
    if with_hourly:
        info.hourly = {
            provider: [
                HourPoint(hour=datetime.fromtimestamp(hour * HOUR_SECONDS, UTC), account_week_pct=pct)
                for hour, pct in sorted(tally.hourly.get(provider, {}).items())
            ]
            for provider in POOL_PROVIDERS
        }
    return info


def sort_key(row: ThreadBurn, pool: str | None) -> float:
    if pool:
        return row.pools[pool].pool_share_pct
    return sum(item.pool_share_pct for item in row.pools.values())


async def build_burn(
    session: AsyncSession,
    summaries: list[AccountSummary],
    b_star: dict[str, float | None],
    *,
    window: str,
    host: str | None = None,
    pool: str | None = None,
    root: str | None = None,
) -> BurnResponse:
    loaded = await load(session, summaries, window)
    thread_rows, session_rows = await T3ThreadsRepository(session).registry()
    native = {item.native_id: item for item in session_rows}
    threads = {(item.host, item.thread_id): item for item in thread_rows}
    sessions_by_thread: dict[tuple[str, str], list[T3NativeSession]] = defaultdict(list)
    for item in session_rows:
        sessions_by_thread[(item.host, item.thread_id)].append(item)

    weights = group_weights(loaded.groups)
    loads: dict[tuple[str, int], dict[int, float]] = defaultdict(dict)
    for index, (group, weight) in enumerate(zip(loaded.groups, weights)):
        loads[(group.account_id, group.hour)][index] = weight
    shares, unexplained_by_account = apportion(loaded.rises, loads)
    pct = [0.0] * len(loaded.groups)
    for per_group in shares.values():
        for index, value in per_group.items():
            pct[index] += value
    unexplained: dict[str, float] = defaultdict(float)
    for (account, hour), rise in loaded.rises.items():
        if (account, hour) not in shares:
            unexplained[loaded.accounts[account]] += rise

    capacity = pool_capacity(summaries)
    hours = max((loaded.now - loaded.start).total_seconds() / HOUR_SECONDS, 1.0)
    totals: dict[str, dict[str, float]] = {name: defaultdict(float) for name in (THREAD, UNREGISTERED, OUTSIDE)}
    requests: dict[str, dict[str, int]] = {name: defaultdict(int) for name in (THREAD, UNREGISTERED, OUTSIDE)}
    roots: dict[tuple[str, str], Tally] = {}
    children: dict[tuple[str, str, str], Tally] = {}
    for group, value in zip(loaded.groups, pct):
        label = label_of(group, native)
        requests[label][group.provider] += group.requests
        if label != THREAD:
            totals[label][group.provider] += value
            continue
        item = native[group.session or ""]
        if host and item.host != host:
            continue
        totals[THREAD][group.provider] += value
        row = threads.get((item.host, item.thread_id))
        root_id = row.root_thread_id if row else item.thread_id
        if root is not None and root_id != root:
            continue
        family = model_family(group, item.driver)
        roots.setdefault((item.host, root_id), Tally()).add(group, value, family)
        if item.thread_id != root_id:
            children.setdefault((item.host, root_id, item.thread_id), Tally()).add(group, value, family)

    pools = {}
    for provider in POOL_PROVIDERS:
        denominator = 100 * capacity.get(provider, 0.0)

        def share(value: float, denominator: float = denominator) -> PoolShare:
            return PoolShare(pool_share_pct=value / denominator * 100 if denominator else 0.0, account_week_pct=value)

        matched, loose = requests[THREAD][provider], requests[UNREGISTERED][provider]
        pools[provider] = PoolBurn(
            t3_threads=share(totals[THREAD][provider]),
            t3_unregistered=share(totals[UNREGISTERED][provider]),
            outside_t3=share(totals[OUTSIDE][provider]),
            unexplained=share(unexplained[provider]),
            matched_request_pct=matched / (matched + loose) * 100 if matched + loose else None,
        )

    subagents: dict[tuple[str, str], int] = defaultdict(int)
    for (row_host, thread_id), row in threads.items():
        if (
            thread_id != row.root_thread_id
            and not thread_id.startswith(DELEGATED_PREFIX)
            and (row_host, thread_id) not in sessions_by_thread
        ):
            subagents[(row_host, row.root_thread_id)] += 1

    rows = []
    for (row_host, root_id), tally in roots.items():
        info = finish(
            thread_info(row_host, root_id, threads, sessions_by_thread),
            tally,
            capacity,
            b_star,
            hours,
            root is not None,
        )
        info.subagent_count = subagents.get((row_host, root_id), 0)
        info.children = sorted(
            (
                finish(thread_info(kid_host, kid_id, threads, sessions_by_thread), kid, capacity, b_star, hours, False)
                for (kid_host, kid_root, kid_id), kid in children.items()
                if (kid_host, kid_root) == (row_host, root_id)
            ),
            key=lambda kid: -sort_key(kid, pool),
        )
        rows.append(info)
    if pool:
        rows = [row for row in rows if row.pools[pool].requests]
    rows.sort(key=lambda row: -sort_key(row, pool))
    return BurnResponse(
        generated_at=loaded.now,
        window_hours=RANGES[window],
        host=host,
        pools=pools,
        threads=rows,
    )
