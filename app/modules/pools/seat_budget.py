from __future__ import annotations

import calendar
import fnmatch
from datetime import datetime, timedelta, timezone

from app.modules.pools.schemas import POOL_STATUS_EXHAUSTED, POOL_STATUS_LOW, POOL_STATUS_OK, PoolSummary
from app.modules.pools.seat_quota import parse_seat_timestamp, pool_quota_source, pool_quota_windows
from app.modules.pools.seat_schemas import SeatAccount, SeatAccountsResponse, SeatModelUsage, SeatRun


def _monthly_cycle(now: datetime, day: int) -> tuple[datetime, datetime]:
    day = max(1, min(31, day))

    def boundary(offset: int) -> datetime:
        month_index = now.year * 12 + now.month - 1 + offset
        year, month = divmod(month_index, 12)
        month += 1
        return datetime(year, month, min(day, calendar.monthrange(year, month)[1]), tzinfo=timezone.utc)

    start = boundary(0)
    return (boundary(-1), start) if start > now else (start, boundary(1))


def cursor_budget_pools(seats: SeatAccountsResponse, config: dict, *, now: datetime) -> list[PoolSummary]:
    cursor = config.get("cursor")
    if not isinstance(cursor, dict):
        return []
    members = [account for account in seats.accounts if account.vendor == "cursor"]
    if not members:
        return []
    raw_pools = cursor.get("pools")
    definitions = [
        pool
        for pool in (raw_pools if isinstance(raw_pools, list) else [])
        if isinstance(pool, dict)
        and isinstance(pool.get("id"), str)
        and pool["id"]
        and isinstance(pool.get("models"), list)
        and pool["models"]
        and all(isinstance(pattern, str) for pattern in pool["models"])
        and isinstance(pool.get("budget_usd_by_tier", {}), dict)
        and all(type(value) in (int, float) and value >= 0 for value in pool.get("budget_usd_by_tier", {}).values())
    ]
    raw_prices = cursor.get("prices_usd_per_mtok")
    prices = [
        entry
        for entry in (raw_prices if isinstance(raw_prices, list) else [])
        if isinstance(entry, list)
        and len(entry) == 2
        and isinstance(entry[0], str)
        and isinstance(entry[1], list)
        and len(entry[1]) == 3
        and all(type(rate) in (int, float) and rate >= 0 for rate in entry[1])
    ]
    cycle_day = cursor.get("cycle_day_default", 1)
    if type(cycle_day) is not int or not 1 <= cycle_day <= 31:
        cycle_day = 1
    fast_multiplier = cursor.get("fast_multiplier", 1)
    if type(fast_multiplier) not in (int, float) or fast_multiplier < 0:
        fast_multiplier = 1
    current = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)

    def membership(model: str) -> int | None:
        if fnmatch.fnmatchcase(model, "*grok-*"):
            return None
        return next(
            (
                index
                for index, pool in enumerate(definitions)
                if any(fnmatch.fnmatchcase(model, pattern) for pattern in pool["models"])
            ),
            None,
        )

    def cost(model: str, usage: SeatModelUsage | SeatRun) -> float:
        for pattern, rates in prices:
            if fnmatch.fnmatchcase(model, pattern):
                multiplier = fast_multiplier if model.endswith("-fast") and not pattern.endswith("-fast") else 1
                return (
                    multiplier
                    * (
                        (usage.tokens_in or 0) * rates[0]
                        + (usage.cache_read or 0) * rates[1]
                        + (usage.tokens_out or 0) * rates[2]
                    )
                    / 1_000_000
                )
        return 0.0

    plans: dict[tuple[str, str], list[SeatAccount]] = {}
    for account in members:
        key = ("plan", account.plan) if account.plan else ("account", account.id)
        plans.setdefault(key, []).append(account)
    pools: list[PoolSummary] = []
    for index, definition in enumerate(definitions):
        ready = sum(
            account.enabled
            and account.auth_ok is not False
            and account.cooldown_until is None
            and not any(
                definition["id"] in peer.cooldowns
                for peer in (plans[("plan", account.plan)] if account.plan else [account])
            )
            for account in members
        )
        vendor_limit = False
        spent = budgeted_spent = burn = budget = 0.0
        unbudgeted = observed_runs = observed_tokens = 0
        cycles: list[tuple[datetime, datetime]] = []
        for plan_members in plans.values():
            budgets = definition.get("budget_usd_by_tier", {})
            account_budget = next((budgets[a.tier] for a in plan_members if a.tier in budgets), None)
            start, reset = _monthly_cycle(current, next((a.cycle_day for a in plan_members if a.cycle_day), cycle_day))
            cycles.append((start, reset))
            plan_spent = plan_burn = 0.0
            for account in plan_members:
                plan_spent += sum(
                    cost(model, usage)
                    for day, models in account.daily.items()
                    if start.date().isoformat() <= day <= current.date().isoformat()
                    for model, usage in models.items()
                    if membership(model) == index
                )
                recent = [
                    run
                    for run in account.recent_runs
                    if run.model
                    and membership(run.model) == index
                    and current - timedelta(hours=24) < run.ts <= current
                ]
                observed_runs += len(recent)
                observed_tokens += sum((run.tokens_in or 0) + (run.tokens_out or 0) for run in recent)
                plan_burn += sum(cost(run.model, run) for run in recent if run.model)
                limit = account.limits.get(definition["id"], {})
                at, until = parse_seat_timestamp(limit.get("at")), parse_seat_timestamp(limit.get("until"))
                if account_budget is not None:
                    vendor_limit |= bool(
                        limit.get("source") == "vendor" and at and until and start <= at <= current < until
                    )
            spent += plan_spent
            if account_budget is None:
                unbudgeted += len(plan_members)
            else:
                budget += account_budget
                budgeted_spent += plan_spent
                burn += plan_burn
        percent_used = (
            100.0 if vendor_limit else (min(100.0, round(100 * budgeted_spent / budget, 1)) if budget > 0 else None)
        )
        percent_source = "vendor" if vendor_limit else ("estimate" if budget > 0 else None)
        remaining = (
            0.0 if vendor_limit else (max(0.0, 100 * (budget - budgeted_spent) / budget) if budget > 0 else None)
        )
        measured_windows = pool_quota_windows(members, definition["id"])
        vendor_windows = [
            window
            for window in measured_windows
            if window.source in ("vendor", "vendor_observed")
            and (window.resets_at is None or window.resets_at > current)
        ]
        if vendor_windows:
            remaining = min(window.remaining_percent for window in vendor_windows)
            percent_used = max(window.used_percent for window in vendor_windows)
            percent_source = "vendor"
            vendor_limit = remaining <= 0
        if remaining == 0 or vendor_limit:
            ready = 0
        start, reset = min(cycles, key=lambda cycle: cycle[1])
        resets = [window.resets_at for window in vendor_windows if window.resets_at]
        if resets:
            reset = min(resets)
        time_remaining = 100 * (reset - current).total_seconds() / (reset - start).total_seconds()
        status = (
            POOL_STATUS_EXHAUSTED
            if not ready or remaining == 0
            else (POOL_STATUS_LOW if remaining is not None and remaining <= 15 else POOL_STATUS_OK)
        )
        pools.append(
            PoolSummary(
                id=definition["id"],
                windows=measured_windows,
                quota_source=pool_quota_source(members),
                provider="cursor",
                kind="cli_seat_budget",
                accounts=len(members),
                eligible_accounts=ready,
                headroom_percent=(remaining if remaining is not None else 100.0) if ready else 0.0,
                aggregate_remaining_percent=remaining,
                status=status,
                source=seats.source,
                window_label="month",
                percent_used=percent_used,
                percent_source=percent_source,
                spent_usd=round(spent, 2),
                budget_usd=budget if budget > 0 else None,
                monthly_remaining_percent=remaining,
                monthly_pace_percent=remaining - time_remaining if remaining is not None else None,
                burn24h_percent=100 * burn / budget if budget > 0 else None,
                cycle_reset_at=reset,
                reset_at=reset if status == POOL_STATUS_EXHAUSTED else None,
                unbudgeted_accounts=unbudgeted,
                observed_runs=observed_runs,
                observed_tokens=observed_tokens,
            )
        )
    return pools
