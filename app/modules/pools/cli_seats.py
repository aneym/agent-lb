from __future__ import annotations

import fnmatch
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.core.routing_table import read_routing_table
from app.modules.pools.schemas import POOL_STATUS_EXHAUSTED, POOL_STATUS_OK, PoolSummary
from app.modules.pools.seat_budget import cursor_budget_pools
from app.modules.pools.seat_quota import parse_seat_timestamp, pool_quota_source, pool_quota_windows
from app.modules.pools.seat_schemas import (
    SeatAccount,
    SeatAccountsResponse,
    SeatAccountUsage,
    SeatModelUsage,
    SeatQuota,
    SeatQuotaWindow,
    SeatRun,
    SeatToday,
    SeatTodayModel,
)

logger = logging.getLogger(__name__)

CLI_SEAT_VENDORS = ("cursor", "devin", "muse")
POOL_KIND_CLI_SEAT = "cli_seat"
POOL_SOURCE_SEAT_STATE = "seat_state"

POOL_SOURCE_SEAT_STATE_STALE = "seat_state_stale"
_STALE_AFTER = timedelta(hours=12)


def seat_state_path() -> Path:
    configured = os.environ.get("AGENT_LB_SEAT_STATE")
    return Path(configured).expanduser() if configured else Path.home() / ".agent-lb" / "seats" / "state.json"


def _read_state(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _usage(record: dict[str, Any], since: datetime) -> SeatAccountUsage:
    runs = [
        run
        for run in record.get("runs") or []
        if isinstance(run, dict) and (parse_seat_timestamp(run.get("ts")) or since) > since
    ]
    return SeatAccountUsage(
        runs=len(runs),
        ok=sum(1 for run in runs if run.get("ok") is True),
        wall_s=round(sum(float(run.get("wall_s") or 0) for run in runs), 1),
        tokens_in=sum(int(run.get("tokens_in") or 0) for run in runs),
        tokens_out=sum(int(run.get("tokens_out") or 0) for run in runs),
    )


def _recovered_pools(record: dict[str, Any]) -> set[str]:
    quota = record.get("quota") if isinstance(record.get("quota"), dict) else {}
    observed = parse_seat_timestamp(quota.get("observedAt"))
    limits = record.get("limits") if isinstance(record.get("limits"), dict) else {}
    return {
        window["id"]
        for window in quota.get("windows") or []
        if isinstance(window, dict)
        and isinstance(window.get("id"), str)
        and isinstance(window.get("usedPercent"), (int, float))
        and window["usedPercent"] < 100
        and observed is not None
        and (at := parse_seat_timestamp((limits.get(window["id"]) or {}).get("at"))) is not None
        and observed > at
    }


def read_seat_accounts(path: Path | None = None, *, now: datetime | None = None) -> SeatAccountsResponse:
    current = now or datetime.now(timezone.utc)
    state = _read_state(path or seat_state_path())
    if state is None:
        return SeatAccountsResponse(source="missing")
    updated = parse_seat_timestamp(state.get("updated_at"))
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
                    and (stamp := parse_seat_timestamp(run.get("ts"))) is not None
                    and current - timedelta(hours=24) < stamp <= current
                ],
                last_day=_usage(record, current - timedelta(hours=24)),
            )
        except (ValidationError, ValueError, TypeError, OverflowError):
            logger.warning("Ignoring malformed seat usage for %s", account_id)
            usage = SeatAccount(id=str(account_id), vendor=record["vendor"])
        try:
            recovered = _recovered_pools(record)
            cooldown = parse_seat_timestamp(record.get("cooldown_until"))
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
                    auth_checked_at=parse_seat_timestamp(record.get("auth_checked_at")),
                    tier=record.get("tier_override") or identity.get("tier"),
                    plan=record.get("plan"),
                    cycle_day=record.get("cycle_day"),
                    daily=usage.daily,
                    recent_runs=usage.recent_runs,
                    cooldown_until=cooldown,
                    cooldowns={
                        pool: stamp
                        for pool, value in (record.get("cooldowns") or {}).items()
                        if pool not in recovered
                        and (stamp := parse_seat_timestamp(value)) is not None
                        and stamp > current
                    },
                    limits={
                        pool: limit for pool, limit in (record.get("limits") or {}).items() if pool not in recovered
                    },
                    last_error_kind=error.get("kind"),
                    last_error_at=parse_seat_timestamp(error.get("ts")),
                    last_run_at=parse_seat_timestamp(record.get("last_run_at")),
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
                    label="Composer" if pool.id == "cursor-models" else "Other models",
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
        quota_refreshed_at=parse_seat_timestamp(state.get("quota_refreshed_at")),
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


def seat_pricing_config() -> dict:
    return read_routing_table("pools").get("cli_pools") or {}


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
