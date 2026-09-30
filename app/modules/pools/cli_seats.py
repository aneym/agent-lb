"""Pools for the CLI-only seats (Cursor, Devin), read from the `seat` CLI's state file.

Their traffic never passes through the LB and neither vendor publishes a usage
endpoint for these plans, so there is no window to read. What the LB can serve is
what `clients/seat` observed: which accounts are registered, whether each one's
auth probe passed, which are cooling down after a limit error, and the runs and
tokens each served. Headroom is therefore availability, not a metered percent:
100 while any account can take work, 0 when none can.
"""

from __future__ import annotations

import calendar
import fnmatch
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError

from app.modules.pools.schemas import POOL_STATUS_EXHAUSTED, POOL_STATUS_LOW, POOL_STATUS_OK, PoolSummary
from app.modules.shared.schemas import DashboardModel

logger = logging.getLogger(__name__)

CLI_SEAT_VENDORS = ("cursor", "devin")
POOL_KIND_CLI_SEAT = "cli_seat"
POOL_SOURCE_SEAT_STATE = "seat_state"
# A state nobody has touched for this long says nothing about auth today.
POOL_SOURCE_SEAT_STATE_STALE = "seat_state_stale"
_STALE_AFTER = timedelta(hours=12)


class SeatAccountUsage(DashboardModel):
    runs: int = 0
    ok: int = 0
    wall_s: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0


class SeatModelUsage(DashboardModel):
    runs: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0


class SeatRun(DashboardModel):
    ts: datetime
    model: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cache_read: int | None = None


class SeatAccount(DashboardModel):
    id: str
    vendor: str
    auth: str | None = None
    enabled: bool = True
    auth_ok: bool | None = None
    auth_checked_at: datetime | None = None
    tier: str | None = None
    cycle_day: int | None = None
    daily: dict[str, dict[str, SeatModelUsage]] = Field(default_factory=dict)
    recent_runs: list[SeatRun] = Field(default_factory=list)
    cooldown_until: datetime | None = None
    last_error_kind: str | None = None
    last_error_at: datetime | None = None
    last_run_at: datetime | None = None
    ready: bool = False
    last_day: SeatAccountUsage = Field(default_factory=SeatAccountUsage)


class SeatAccountsResponse(DashboardModel):
    state_updated_at: datetime | None = None
    source: str
    accounts: list[SeatAccount] = Field(default_factory=list)


def seat_state_path() -> Path:
    configured = os.environ.get("AGENT_LB_SEAT_STATE")
    return Path(configured).expanduser() if configured else Path.home() / ".agent-lb" / "seats" / "state.json"


def _ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _read_state(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _usage(record: dict[str, Any], since: datetime) -> SeatAccountUsage:
    runs = [run for run in record.get("runs") or [] if isinstance(run, dict) and (_ts(run.get("ts")) or since) > since]
    return SeatAccountUsage(
        runs=len(runs),
        ok=sum(1 for run in runs if run.get("ok") is True),
        wall_s=round(sum(float(run.get("wall_s") or 0) for run in runs), 1),
        tokens_in=sum(int(run.get("tokens_in") or 0) for run in runs),
        tokens_out=sum(int(run.get("tokens_out") or 0) for run in runs),
    )


def read_seat_accounts(path: Path | None = None, *, now: datetime | None = None) -> SeatAccountsResponse:
    current = now or datetime.now(timezone.utc)
    state = _read_state(path or seat_state_path())
    if state is None:
        return SeatAccountsResponse(source="missing")
    updated = _ts(state.get("updated_at"))
    stale = updated is None or current - updated > _STALE_AFTER
    accounts: list[SeatAccount] = []
    records = state.get("accounts") if isinstance(state.get("accounts"), dict) else {}
    for account_id, record in sorted(records.items()):
        if not isinstance(record, dict) or record.get("vendor") not in CLI_SEAT_VENDORS:
            continue
        try:
            usage = SeatAccount(
                id=str(account_id),
                vendor=record["vendor"],
                daily=record.get("daily") or {},
                recent_runs=[
                    SeatRun(
                        ts=stamp,
                        model=run.get("model"),
                        tokens_in=run.get("tokens_in"),
                        tokens_out=run.get("tokens_out"),
                        cache_read=run.get("cache_read"),
                    )
                    for run in record.get("runs") or []
                    if isinstance(run, dict)
                    and (stamp := _ts(run.get("ts"))) is not None
                    and current - timedelta(hours=24) < stamp <= current
                ],
                last_day=_usage(record, current - timedelta(hours=24)),
            )
        except (ValidationError, ValueError, TypeError, OverflowError):
            logger.warning("Ignoring malformed seat usage for %s", account_id)
            usage = SeatAccount(id=str(account_id), vendor=record["vendor"])
        try:
            cooldown = _ts(record.get("cooldown_until"))
            if cooldown is not None and cooldown <= current:
                cooldown = None
            enabled = record.get("enabled") is not False
            error = record.get("last_error") if isinstance(record.get("last_error"), dict) else {}
            identity = record.get("identity") if isinstance(record.get("identity"), dict) else {}
            accounts.append(
                SeatAccount(
                    id=str(account_id),
                    vendor=record["vendor"],
                    auth=record.get("auth"),
                    enabled=enabled,
                    auth_ok=record.get("auth_ok"),
                    auth_checked_at=_ts(record.get("auth_checked_at")),
                    tier=record.get("tier_override") or identity.get("tier"),
                    cycle_day=record.get("cycle_day"),
                    daily=usage.daily,
                    recent_runs=usage.recent_runs,
                    cooldown_until=cooldown,
                    last_error_kind=error.get("kind"),
                    last_error_at=_ts(error.get("ts")),
                    last_run_at=_ts(record.get("last_run_at")),
                    ready=enabled and record.get("auth_ok") is not False and cooldown is None,
                    last_day=usage.last_day,
                )
            )
        except (ValidationError, ValueError, TypeError):
            logger.warning("Skipping malformed seat account %s", account_id)
    return SeatAccountsResponse(
        state_updated_at=updated,
        source=POOL_SOURCE_SEAT_STATE_STALE if stale else POOL_SOURCE_SEAT_STATE,
        accounts=accounts,
    )


def cli_seat_pools(seats: SeatAccountsResponse) -> list[PoolSummary]:
    pools: list[PoolSummary] = []
    for vendor in CLI_SEAT_VENDORS:
        members = [account for account in seats.accounts if account.vendor == vendor]
        if not members:
            continue
        ready = [account for account in members if account.ready]
        cooling = [account.cooldown_until for account in members if account.cooldown_until is not None]
        headroom = 100.0 if ready else 0.0
        pools.append(
            PoolSummary(
                id=vendor,
                provider=vendor,
                kind=POOL_KIND_CLI_SEAT,
                accounts=len(members),
                eligible_accounts=len(ready),
                headroom_percent=headroom,
                aggregate_remaining_percent=100.0 * len(ready) / len(members),
                reset_at=None if ready or not cooling else min(cooling),
                status=POOL_STATUS_OK if ready else POOL_STATUS_EXHAUSTED,
                source=seats.source,
                observed_runs=sum(account.last_day.runs for account in members),
                observed_tokens=sum(account.last_day.tokens_in + account.last_day.tokens_out for account in members),
            )
        )
    return pools


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

    pools: list[PoolSummary] = []
    ready = sum(account.ready for account in members)
    for index, definition in enumerate(definitions):
        spent = budgeted_spent = burn = budget = 0.0
        unbudgeted = observed_runs = observed_tokens = 0
        cycles: list[tuple[datetime, datetime]] = []
        for account in members:
            start, reset = _monthly_cycle(current, account.cycle_day or cycle_day)
            cycles.append((start, reset))
            account_spent = sum(
                cost(model, usage)
                for day, models in account.daily.items()
                if start.date().isoformat() <= day <= current.date().isoformat()
                for model, usage in models.items()
                if membership(model) == index
            )
            recent = [
                run
                for run in account.recent_runs
                if run.model and membership(run.model) == index and current - timedelta(hours=24) < run.ts <= current
            ]
            spent += account_spent
            observed_runs += len(recent)
            observed_tokens += sum((run.tokens_in or 0) + (run.tokens_out or 0) for run in recent)
            account_budget = definition.get("budget_usd_by_tier", {}).get(account.tier)
            if account_budget is None:
                unbudgeted += 1
            else:
                budget += account_budget
                budgeted_spent += account_spent
                burn += sum(cost(run.model, run) for run in recent if run.model)
        remaining = max(0.0, 100 * (budget - budgeted_spent) / budget) if budget > 0 else None
        start, reset = min(cycles, key=lambda cycle: cycle[1])
        time_remaining = 100 * (reset - current).total_seconds() / (reset - start).total_seconds()
        status = (
            POOL_STATUS_EXHAUSTED
            if not ready or remaining == 0
            else (POOL_STATUS_LOW if remaining is not None and remaining <= 15 else POOL_STATUS_OK)
        )
        pools.append(
            PoolSummary(
                id=definition["id"],
                provider="cursor",
                kind="cli_seat_budget",
                accounts=len(members),
                eligible_accounts=ready,
                headroom_percent=(remaining if remaining is not None else 100.0) if ready else 0.0,
                aggregate_remaining_percent=remaining,
                status=status,
                source=seats.source,
                window_label="month",
                spent_usd=round(spent, 2),
                budget_usd=budget if budget > 0 else None,
                monthly_remaining_percent=remaining,
                monthly_pace_percent=remaining - time_remaining if remaining is not None else None,
                burn24h_percent=100 * burn / budget if budget > 0 else None,
                cycle_reset_at=reset,
                unbudgeted_accounts=unbudgeted,
                observed_runs=observed_runs,
                observed_tokens=observed_tokens,
            )
        )
    return pools
