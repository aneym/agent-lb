from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

OURS = "primary"
TEAMMATE = "teammate-owned"
GROUPS = (("ours", OURS), ("teammate", TEAMMATE))
PACE_FIELDS = ("weekly_all_points_last_1h", "weekly_all_points_last_24h")

RULE = (
    "ours is every account with owner primary (this agent-lb holds its sign-in and can redeem its resets); teammate is "
    "every account with owner teammate-owned (a teammate's agent-lb pushes it here, and use on it can be the teammate's "
    "own). Each group's points_left is the sum of its accounts' weekly_all_points_left; usable_points_left counts only "
    "accounts with usable_now; the pace is the sum of the group's account figures that are not null "
    "(accounts_without_coverage names the rest, so the pace is then a floor); hours_left is points_left over that pace, "
    "null when no points moved. Percent of quota only, never cost."
)


def _hours(points: float, moved: float | None, hours: int) -> float | None:
    if moved is None or moved <= 0:
        return None
    return round(points / (moved / hours), 2)


def _next_reset(entries: list[dict[str, Any]], now: datetime) -> tuple[str | None, float | None]:
    aware = now.replace(tzinfo=timezone.utc)
    upcoming = sorted(
        moment
        for moment in (
            datetime.fromisoformat(entry["weekly_all_resets"].replace("Z", "+00:00"))
            for entry in entries
            if entry.get("weekly_all_resets")
        )
        if moment > aware
    )
    if not upcoming:
        return None, None
    first = upcoming[0]
    return first.isoformat().replace("+00:00", "Z"), round((first - aware).total_seconds() / 3600, 2)


def group(entries: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    points_left = round(sum(float(entry["weekly_all_points_left"]) for entry in entries), 2)
    usable = round(sum(float(entry["weekly_all_points_left"]) for entry in entries if entry["usable_now"]), 2)
    moved: dict[str, float | None] = {}
    missing: dict[str, list[str]] = {}
    for field in PACE_FIELDS:
        figures = [entry.get(field) for entry in entries]
        missing[field] = [entry["account"] for entry, value in zip(entries, figures) if value is None]
        counted = [float(value) for value in figures if value is not None]
        moved[field] = round(sum(counted), 2) if counted else None
    per_hour_1h = moved["weekly_all_points_last_1h"]
    per_hour_24h = (
        None if moved["weekly_all_points_last_24h"] is None else round(moved["weekly_all_points_last_24h"] / 24, 4)
    )
    reset, reset_hours = _next_reset(entries, now)
    return {
        "accounts": [entry["account"] for entry in entries],
        "accounts_with_room": sum(1 for entry in entries if entry["weekly_all_points_left"] > 0),
        "usable_now": [entry["account"] for entry in entries if entry["usable_now"]],
        "points_left": points_left,
        "usable_points_left": usable,
        "weekly_all_points_last_24h": moved["weekly_all_points_last_24h"],
        "points_per_hour_last_1h": per_hour_1h,
        "points_per_hour_24h": per_hour_24h,
        "hours_left_last_hour_rate": _hours(points_left, per_hour_1h, 1),
        "hours_left_24h_rate": _hours(points_left, moved["weekly_all_points_last_24h"], 24),
        "next_weekly_reset": reset,
        "next_weekly_reset_hours": reset_hours,
        "accounts_without_coverage": missing,
    }


def apply(entries: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    split: dict[str, Any] = {
        name: group([entry for entry in entries if entry.get("owner") == owner], now) for name, owner in GROUPS
    }
    split["rule"] = RULE
    return {"points_by_owner": split}
