from __future__ import annotations

import calendar
import fnmatch
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import Field, ValidationError

from app.modules.pools.schemas import POOL_STATUS_EXHAUSTED, POOL_STATUS_LOW, POOL_STATUS_OK, PoolSummary, PoolWindow
from app.modules.shared.schemas import DashboardModel

logger = logging.getLogger(__name__)

CLI_SEAT_VENDORS = ("cursor", "devin", "muse")
POOL_KIND_CLI_SEAT = "cli_seat"
POOL_SOURCE_SEAT_STATE = "seat_state"

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
    ok: int | None = Field(default=None, exclude=True)
    tokens_known: bool = Field(default=False, exclude=True)


class SeatQuotaWindow(DashboardModel):
    id: str
    kind: str
    label: str
    duration_mins: int | None = None
    used_percent: float | None = None
    remaining_percent: float | None = None
    resets_at: datetime | None = None
    source: str


class SeatQuota(DashboardModel):
    source: str = "unavailable"
    observed_at: datetime | None = None
    stale: bool = False
    unavailable_reason: str | None = "not_collected"
    primary_window_id: str | None = None
    windows: list[SeatQuotaWindow] = Field(default_factory=list)


class SeatTodayModel(DashboardModel):
    model: str
    runs: int = 0
    tokens_in: int | None = None
    tokens_out: int | None = None


class SeatToday(DashboardModel):
    runs: int = 0
    ok: int = 0
    ok_known: bool = True
    tokens_in: int | None = None
    tokens_out: int | None = None
    tokens_known: bool = False
    value_usd: float | None = None
    value_source: str | None = None
    models: list[SeatTodayModel] = Field(default_factory=list)


class SeatRun(DashboardModel):
    ok: bool | None = None
    wall_s: float | None = None
    ts: datetime
    model: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cache_read: int | None = None


class SeatAccount(DashboardModel):
    email: str | None = None
    plan_label: str | None = None
    status: str = "active"
    status_reset_at: datetime | None = None
    quota: SeatQuota = Field(default_factory=SeatQuota)
    today: SeatToday = Field(default_factory=SeatToday)
    id: str
    vendor: str
    auth: str | None = None
    enabled: bool = True
    auth_ok: bool | None = None
    auth_checked_at: datetime | None = None
    tier: str | None = None
    plan: str | None = None
    cycle_day: int | None = None
    daily: dict[str, dict[str, SeatModelUsage]] = Field(default_factory=dict)
    recent_runs: list[SeatRun] = Field(default_factory=list)
    cooldown_until: datetime | None = None
    cooldowns: dict[str, datetime] = Field(default_factory=dict)
    limits: dict[str, dict[str, Any]] = Field(default_factory=dict, exclude=True)
    last_error_kind: str | None = None
    last_error_at: datetime | None = None
    last_run_at: datetime | None = None
    ready: bool = False
    last_day: SeatAccountUsage = Field(default_factory=SeatAccountUsage)


class SeatAccountsResponse(DashboardModel):
    quota_refreshed_at: datetime | None = None
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
                daily={
                    day: {
                        model: {
                            **bucket,
                            "tokens_known": bucket.get(
                                "tokens_known", (bucket.get("tokens_in", 0) + bucket.get("tokens_out", 0)) > 0
                            ),
                        }
                        for model, bucket in models.items()
                    }
                    for day, models in (record.get("daily") or {}).items()
                },
                recent_runs=[
                    SeatRun(
                        ts=stamp,
                        model=run.get("model"),
                        ok=run.get("ok"),
                        wall_s=run.get("wall_s"),
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
                    email=record.get("email") or identity.get("email"),
                    plan_label=record.get("plan_label")
                    or record.get("tier_override")
                    or identity.get("tier")
                    or record.get("plan"),
                    quota=SeatQuota.model_validate(
                        record.get("quota")
                        or {"unavailableReason": "not_reported" if record["vendor"] == "muse" else "not_collected"}
                    ),
                    today=seat_activity([usage], current, days=1),
                    enabled=enabled,
                    auth_ok=record.get("auth_ok"),
                    auth_checked_at=_ts(record.get("auth_checked_at")),
                    tier=record.get("tier_override") or identity.get("tier"),
                    plan=record.get("plan"),
                    cycle_day=record.get("cycle_day"),
                    daily=usage.daily,
                    recent_runs=usage.recent_runs,
                    cooldown_until=cooldown,
                    cooldowns={
                        pool: stamp
                        for pool, value in (record.get("cooldowns") or {}).items()
                        if (stamp := _ts(value)) is not None and stamp > current
                    },
                    limits=record.get("limits") or {},
                    last_error_kind=error.get("kind"),
                    last_error_at=_ts(error.get("ts")),
                    last_run_at=_ts(record.get("last_run_at")),
                    ready=enabled and record.get("auth_ok") is not False and cooldown is None,
                    last_day=usage.last_day,
                )
            )
        except (ValidationError, ValueError, TypeError):
            logger.warning("Skipping malformed seat account %s", account_id)
    for account in accounts:
        if account.vendor == "cursor" and account.plan:
            peers = [peer for peer in accounts if peer.vendor == "cursor" and peer.plan == account.plan]
            for peer in peers:
                for pool, until in peer.cooldowns.items():
                    account.cooldowns[pool] = max(until, account.cooldowns.get(pool, until))
    config = seat_pricing_config()
    for account in accounts:
        if account.vendor == "cursor" and not account.quota.windows:
            estimates = cursor_budget_pools(
                SeatAccountsResponse(
                    source="estimate",
                    accounts=[
                        peer
                        for peer in accounts
                        if peer.id == account.id or (account.plan and peer.plan == account.plan)
                    ],
                ),
                config,
                now=current,
            )
            windows = [
                SeatQuotaWindow(
                    id=pool.id,
                    kind="monthly",
                    label="Cursor models · Grok, Composer" if pool.id == "cursor-models" else "Other models",
                    used_percent=pool.percent_used,
                    remaining_percent=pool.aggregate_remaining_percent,
                    resets_at=pool.cycle_reset_at,
                    source="estimate",
                )
                for pool in estimates
                if pool.percent_used is not None
            ]
            if windows:
                account.quota = SeatQuota(
                    source="estimate",
                    windows=windows,
                    primary_window_id=max(windows, key=lambda window: window.used_percent or 0).id,
                    unavailable_reason=account.quota.unavailable_reason,
                )
        if account.quota.observed_at and current - account.quota.observed_at > timedelta(minutes=15):
            account.quota.stale = True
        resets = [until for until in [account.cooldown_until, *account.cooldowns.values()] if until]
        limited = [
            window
            for window in account.quota.windows
            if (window.used_percent or 0) >= 100 and (window.resets_at is None or window.resets_at > current)
        ]
        resets.extend(window.resets_at for window in limited if window.resets_at)
        account.status = (
            "paused"
            if not account.enabled
            else "reauth_required"
            if account.auth_ok is False
            else "rate_limited"
            if resets or limited
            else "active"
        )
        account.status_reset_at = min(resets) if resets and account.status == "rate_limited" else None
        account.ready = account.ready and account.status != "rate_limited"
    return SeatAccountsResponse(
        quota_refreshed_at=_ts(state.get("quota_refreshed_at")),
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
        current = datetime.now(timezone.utc)
        exhausted = [
            window
            for account in members
            for window in account.quota.windows
            if (window.used_percent or 0) >= 100 and window.resets_at and window.resets_at > current
        ]
        ready = [
            account
            for account in members
            if account.ready
            and not any(
                (window.used_percent or 0) >= 100 and window.resets_at and window.resets_at > current
                for window in account.quota.windows
            )
        ]
        cooling = [account.cooldown_until for account in members if account.cooldown_until is not None]
        cooling.extend(window.resets_at for window in exhausted)
        measured = [
            window.remaining_percent
            for account in ready
            for window in account.quota.windows
            if window.used_percent is not None and window.remaining_percent is not None
        ]
        headroom = min(measured) if measured else None
        windows = pool_quota_windows(members)
        pools.append(
            PoolSummary(
                id=vendor,
                provider=vendor,
                kind=POOL_KIND_CLI_SEAT,
                accounts=len(members),
                eligible_accounts=len(ready),
                headroom_percent=headroom,
                aggregate_remaining_percent=headroom,
                reset_at=min(cooling) if cooling else None,
                status=POOL_STATUS_OK if ready else POOL_STATUS_EXHAUSTED,
                source=seats.source,
                windows=windows,
                window_label=(
                    "month"
                    if any(w.kind == "monthly" for w in windows)
                    else "week"
                    if any(w.kind == "weekly" for w in windows)
                    else None
                ),
                unavailable_reason=next(
                    (a.quota.unavailable_reason for a in members if a.quota.unavailable_reason), None
                ),
                quota_source=pool_quota_source(members),
                observed_runs=sum(account.last_day.runs for account in members),
                observed_tokens=(
                    None
                    if vendor == "muse"
                    else sum(account.last_day.tokens_in + account.last_day.tokens_out for account in members)
                ),
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
                at, until = _ts(limit.get("at")), _ts(limit.get("until"))
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


def seat_pricing_config() -> dict:
    managed = Path.home() / ".agent-lb/managed/coding-agents/routing-table.json"
    path = (
        Path(os.environ["ROUTE_TABLE"]).expanduser()
        if os.environ.get("ROUTE_TABLE")
        else (
            managed
            if managed.is_file()
            else Path(__file__).resolve().parents[3] / "config/coding-agents/routing-table.json"
        )
    )
    document = _read_state(path) or {}
    return document.get("cli_pools") or {}


def seat_activity(accounts: list[SeatAccount], current: datetime, *, days: int = 7) -> SeatToday:
    if days == 1:
        rolling = []
        for account in accounts:
            entries: dict[str, SeatModelUsage] = {}
            for run in account.recent_runs:
                if not run.model or not current - timedelta(hours=24) < run.ts <= current:
                    continue
                entry = entries.setdefault(run.model, SeatModelUsage(tokens_known=True, ok=0))
                entry.runs += 1
                entry.ok = (entry.ok + int(run.ok)) if entry.ok is not None and run.ok is not None else None
                entry.tokens_known &= run.tokens_in is not None and run.tokens_out is not None
                entry.tokens_in += run.tokens_in or 0
                entry.tokens_out += run.tokens_out or 0
                entry.cache_read += run.cache_read or 0
            date = current.astimezone(ZoneInfo("America/New_York")).date().isoformat()
            rolling.append(account.model_copy(update={"daily": {date: entries}}))
        return seat_activity(rolling, current, days=7)
    day = current.astimezone(ZoneInfo("America/New_York")).date()
    start = (day - timedelta(days=days - 1)).isoformat()
    models: dict[str, SeatTodayModel] = {}
    runs = ok = 0
    known = True
    ok_known = True
    unknown_models: set[str] = set()
    value = 0.0
    priced = True
    config = seat_pricing_config().get("cursor") or {}
    prices = config.get("prices_usd_per_mtok") or []
    for account in accounts:
        for date, entries in account.daily.items():
            if not start <= date <= day.isoformat():
                continue
            for model, usage in entries.items():
                target = models.setdefault(model, SeatTodayModel(model=model, tokens_in=0, tokens_out=0))
                target.runs += usage.runs
                runs += usage.runs
                ok += usage.ok or 0
                ok_known &= usage.ok is not None
                known &= usage.tokens_known
                if usage.tokens_known and model not in unknown_models:
                    target.tokens_in = (target.tokens_in or 0) + usage.tokens_in
                    target.tokens_out = (target.tokens_out or 0) + usage.tokens_out
                else:
                    unknown_models.add(model)
                    target.tokens_in = target.tokens_out = None
                rates = next((rates for pattern, rates in prices if fnmatch.fnmatchcase(model, pattern)), None)
                if rates and usage.tokens_known and account.vendor == "cursor":
                    multiplier = config.get("fast_multiplier", 1) if model.endswith("-fast") else 1
                    value += (
                        multiplier
                        * (usage.tokens_in * rates[0] + usage.cache_read * rates[1] + usage.tokens_out * rates[2])
                        / 1_000_000
                    )
                else:
                    priced = False
    known &= runs > 0
    return SeatToday(
        runs=runs,
        ok=ok,
        ok_known=ok_known,
        tokens_known=known,
        tokens_in=sum(item.tokens_in or 0 for item in models.values()) if known else None,
        tokens_out=sum(item.tokens_out or 0 for item in models.values()) if known else None,
        value_usd=round(value, 6) if priced and runs else None,
        value_source="estimate" if priced and runs else None,
        models=list(models.values()),
    )


def pool_quota_source(accounts: list[SeatAccount]) -> str:
    sources = {account.quota.source for account in accounts if account.quota.windows}
    return "mixed" if len(sources) > 1 else next(iter(sources), "unavailable")


def pool_quota_windows(accounts: list[SeatAccount], window_id: str | None = None) -> list[PoolWindow]:
    grouped: dict[str, list[SeatQuotaWindow]] = {}
    seen: dict[tuple[str, str], SeatQuotaWindow] = {}
    for account in accounts:
        for window in account.quota.windows:
            key = (account.plan or account.id, window.id)
            if window_id is not None and window.id != window_id:
                continue
            previous = seen.get(key)
            priority = {"estimate": 0, "vendor_observed": 1, "vendor": 2}
            if previous is None or priority.get(window.source, 0) > priority.get(previous.source, 0):
                seen[key] = window
    for (_, key), window in seen.items():
        grouped.setdefault(key, []).append(window)
    result = []
    for key, windows in grouped.items():
        measured = [window for window in windows if window.used_percent is not None]
        if not measured:
            continue
        resets = [window.resets_at for window in measured if window.resets_at]
        result.append(
            PoolWindow(
                id=key,
                kind=measured[0].kind,
                label=measured[0].label,
                used_percent=sum(window.used_percent for window in measured) / len(measured),
                remaining_percent=sum(window.remaining_percent or 0 for window in measured) / len(measured),
                resets_at=min(resets) if resets else None,
                measured_accounts=sum(
                    any(w.id == key and w.used_percent is not None for w in a.quota.windows) for a in accounts
                ),
                source=(measured[0].source if len({window.source for window in measured}) == 1 else "mixed"),
            )
        )
    return result
