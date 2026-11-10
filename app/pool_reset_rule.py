from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.claude_reset_week import FULL_PERCENT, week_cap, week_start

POINTS_FLOOR = 28.0
MINUTES_FLOOR = 45.0
STALE_MINUTES = 15
GAP_MINUTES = 60
MIN_FREED_POINTS = 10.0
MEASURED_RATIO = round(19 / 39, 4)
MEASURED_RATIO_SOURCE = "502ebd48 reset 2026-11-10T03:17Z: Fable 39% to 0%, all-models 100% to 81%"
RATIO_MIN_FABLE_POINTS = 5.0
SERVING_STATUSES = {"active", "rate_limited", "quota_exceeded"}


@dataclass(frozen=True, slots=True)
class Redemption:
    account: str
    at: datetime
    confirmed: bool


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _age_minutes(value: str | None, now: datetime) -> float | None:
    moment = _parse(value)
    return None if moment is None else (now - moment).total_seconds() / 60


def _stale(account: dict[str, Any], now: datetime) -> bool:
    age = _age_minutes(account.get("limits_recorded_at"), now)
    return age is None or age > STALE_MINUTES


def _ours(headroom: dict[str, Any], owned: set[str]) -> list[dict[str, Any]]:
    return [a for a in headroom["accounts_now"] if a["account"] in owned]


def pace(headroom: dict[str, Any], owned: set[str]) -> float | None:
    figures = [a.get("weekly_all_points_last_1h") for a in _ours(headroom, owned)]
    counted = [float(value) for value in figures if value is not None]
    return round(sum(counted), 2) if counted else None


def usable_points(headroom: dict[str, Any], owned: set[str]) -> float:
    counted = [a for a in _ours(headroom, owned) if a["usable_now"]]
    return round(sum(float(a["weekly_all_points_left"]) for a in counted), 2)


def points_left(headroom: dict[str, Any], owned: set[str]) -> float:
    return round(sum(float(a["weekly_all_points_left"]) for a in _ours(headroom, owned)), 2)


def banked_total(headroom: dict[str, Any]) -> int:
    return sum(int(a["resets_held"] or 0) for a in headroom["accounts_now"])


def minutes_left(points: float, per_hour: float | None) -> float | None:
    if per_hour is None or per_hour <= 0:
        return None
    return round(points / per_hour * 60, 1)


def _ratio(account: dict[str, Any]) -> tuple[float, str]:
    fable = account.get("fable_points_last_24h")
    weekly = account.get("weekly_all_points_last_24h")
    measured = f"measured on {MEASURED_RATIO_SOURCE}"
    if fable is None or weekly is None or float(fable) < RATIO_MIN_FABLE_POINTS:
        return MEASURED_RATIO, measured
    own = round(float(weekly) / float(fable), 4)
    source = f"own last 24 h: {weekly:g} all-models over {fable:g} Fable points = {own:g}"
    if own < MEASURED_RATIO:
        return own, source
    return MEASURED_RATIO, f"{source}, above the {measured} ({MEASURED_RATIO:g}), which caps it"


def _full(account: dict[str, Any]) -> bool:
    return (account.get("weekly_all_used_pct") or 0.0) >= FULL_PERCENT


def candidates(
    headroom: dict[str, Any], owned: set[str], teammate_yes: set[str], now: datetime
) -> tuple[list[dict[str, Any]], list[str]]:
    ranked: list[dict[str, Any]] = []
    skipped: list[str] = []
    for account in headroom["accounts_now"]:
        name = account["account"]
        fable = account.get("fable_weekly_used_pct")
        held = account.get("resets_held")
        if str(account["status"]).lower() not in SERVING_STATUSES:
            skipped.append(f"{name}: status {account['status']}")
            continue
        if not held:
            skipped.append(f"{name}: no banked reset read")
            continue
        if not fable:
            skipped.append(f"{name}: no Fable use to free")
            continue
        ratio, source = _ratio(account)
        freed = round(min(float(fable) * ratio, float(account.get("weekly_all_used_pct") or 0.0)), 1)
        age = _age_minutes(account.get("limits_recorded_at"), now)
        entry = {
            "account": name,
            "fable_weekly_used_pct": fable,
            "weekly_all_used_pct": account.get("weekly_all_used_pct"),
            "resets_held": held,
            "ratio": ratio,
            "ratio_source": source,
            "freed_points_estimate": freed,
            "reading_age_min": None if age is None else round(age, 1),
        }
        if name not in owned:
            yes = "a yes is on file, but" if name in teammate_yes else "no yes on file, and"
            skipped.append(f"{name}: teammate-owned, frees ~{freed:g}; {yes} only its owner's agent-lb can redeem it")
        elif not _full(account):
            skipped.append(f"{name}: week under {FULL_PERCENT:g}% used, and a reset needs a full account")
        elif freed < MIN_FREED_POINTS:
            skipped.append(f"{name}: frees ~{freed:g} points, under {MIN_FREED_POINTS:g}")
        elif age is None or age > STALE_MINUTES:
            skipped.append(f"{name}: reading {age if age is None else round(age)} min old")
        else:
            ranked.append(entry)
    ranked.sort(key=lambda entry: (-entry["freed_points_estimate"], -entry["resets_held"]))
    return ranked, skipped


def decide(
    headroom: dict[str, Any],
    *,
    now: datetime,
    owned: set[str],
    redemptions: list[Redemption],
    held: bool,
    lanes: dict[str, Any],
    teammate_yes: set[str] | None = None,
) -> dict[str, Any]:
    start = week_start(now)
    cap = week_cap(start)
    this_week = [r for r in redemptions if r.at >= start]
    stale = [a["account"] for a in _ours(headroom, owned) if a["usable_now"] and _stale(a, now)]
    points = usable_points(headroom, owned)
    per_hour = pace(headroom, owned)
    minutes = minutes_left(points, per_hour)
    generated_age = _age_minutes(headroom.get("generated_at"), now)
    ranked, skipped = candidates(headroom, owned, teammate_yes or set(), now)
    triggers = []
    if points <= POINTS_FLOOR:
        triggers.append(f"our usable points {points:g} <= {POINTS_FLOOR:g}")
    if minutes is not None and minutes < MINUTES_FLOOR:
        triggers.append(f"{minutes:g} min left at the last hour's pace < {MINUTES_FLOOR:g}")
    if not any(a["usable_now"] for a in _ours(headroom, owned)):
        triggers.append("no usable account of ours")
    latest = max(this_week, key=lambda r: r.at, default=None)
    unconfirmed = [r for r in this_week if not r.confirmed]
    if held:
        action, reason = "hold", "hold file present"
    elif unconfirmed:
        action, reason = "confirm", f"redemption on {unconfirmed[0].account} not yet confirmed"
    elif len(this_week) >= cap:
        action, reason = "capped", f"{len(this_week)} this week, the most allowed ({cap})"
    elif latest is not None and now - latest.at < timedelta(minutes=GAP_MINUTES):
        action, reason = "spacing", f"last redemption {int((now - latest.at).total_seconds() // 60)} min ago"
    elif generated_age is None or generated_age > STALE_MINUTES or stale:
        action, reason = "stale", f"readings older than {STALE_MINUTES} min: {', '.join(stale) or 'export'}"
    elif not triggers:
        action, reason = "wait", "our own accounts are not about to run dry"
    elif not lanes["lanes"]:
        action, reason = "no_named_lane", f"no lane on the ordered list would pause ({lanes['why']})"
    elif not ranked:
        action, reason = (
            "no_candidate",
            f"nothing worth spending: no owned account frees {MIN_FREED_POINTS:g} points or more",
        )
    else:
        action, reason = "redeem", "; ".join(triggers) + f"; a named lane would pause, first {lanes['lanes'][-1]}"
    return {
        "action": action,
        "reason": reason,
        "now": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "week_start": start.isoformat().replace("+00:00", "Z"),
        "week_cap": cap,
        "week_used": len(this_week),
        "week_redemptions": [
            {"account": r.account, "at": r.at.isoformat(), "confirmed": r.confirmed} for r in this_week
        ],
        "usable_points": points,
        "points_left": points_left(headroom, owned),
        "counted_accounts": sorted(a["account"] for a in _ours(headroom, owned)),
        "points_per_hour_last_1h": per_hour,
        "minutes_left_at_last_hour_pace": minutes,
        "triggers": triggers,
        "stale_usable_accounts": stale,
        "named_lanes": len(lanes["lanes"]),
        "lane_order": {key: value for key, value in lanes.items() if key != "lanes"},
        "would_take": ranked[0]["account"] if ranked and action != "no_named_lane" else None,
        "candidates": ranked,
        "skipped": skipped,
        "banked_total": banked_total(headroom),
    }
