from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.usage_export_sql import rows

FABLE_MODEL = "claude-fable-5-2"
FABLE_RATE_HOURS = (1, 6)
POINT_HOURS = 6
MIN_POINTS = 2.0
RESET_DROP_POINTS = 5.0

_FABLE_COST_SQL = """
SELECT coalesce(sum(cost_usd), 0) AS cost_usd, count(*) AS requests,
       count(*) FILTER (WHERE cost_usd IS NULL) AS rows_without_cost
FROM request_logs
WHERE deleted_at IS NULL AND model = %(model)s AND requested_at >= %(start)s AND requested_at < %(end)s
"""

_ACCOUNT_COST_SQL = """
SELECT r.account_id AS id, coalesce(sum(r.cost_usd), 0) AS cost_usd
FROM request_logs r
JOIN accounts a ON a.id = r.account_id AND a.provider = 'anthropic'
WHERE r.deleted_at IS NULL AND r.requested_at >= %(start)s AND r.requested_at < %(end)s
GROUP BY 1
"""

_WEEKLY_READINGS_SQL = """
SELECT h.account_id AS id, h.used_percent
FROM usage_history h
JOIN accounts a ON a.id = h.account_id AND a.provider = 'anthropic'
WHERE h.window = 'secondary' AND h.recorded_at >= %(start)s AND h.recorded_at < %(end)s
ORDER BY h.account_id, h.recorded_at
"""


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _ratio(numerator: float, denominator: float | None, digits: int = 4) -> float | None:
    if not denominator:
        return None
    return round(numerator / denominator, digits)


def fable_usd_per_hour(cursor: Any, now: datetime) -> dict[str, Any]:
    burn: dict[str, Any] = {"model": FABLE_MODEL, "basis": "stored cost_usd of request_logs rows, every status"}
    for hours in FABLE_RATE_HOURS:
        row = rows(cursor, _FABLE_COST_SQL, {"model": FABLE_MODEL, "start": now - timedelta(hours=hours), "end": now})[
            0
        ]
        cost = float(row["cost_usd"])
        burn[f"last_{hours}h"] = {
            "hours": hours,
            "cost_usd": round(cost, 4),
            "usd_per_hour": round(cost / hours, 4),
            "requests": int(row["requests"]),
            "rows_without_cost": int(row["rows_without_cost"]),
        }
    return burn


def _points_moved(readings: list[float]) -> float:
    moved = 0.0
    start = previous = None
    for value in readings:
        if start is None:
            start = previous = value
            continue
        if previous is not None and value < previous - RESET_DROP_POINTS:
            moved += max(0.0, previous - start)
            start = value
        previous = value
    if start is not None and previous is not None:
        moved += max(0.0, previous - start)
    return moved


def _account_cost(cursor: Any, start: datetime, end: datetime) -> dict[str, float]:
    return {row["id"]: float(row["cost_usd"]) for row in rows(cursor, _ACCOUNT_COST_SQL, {"start": start, "end": end})}


def apply(cursor: Any, now: datetime, ids: list[str], entries: list[dict[str, Any]]) -> dict[str, Any]:
    window = {"start": now - timedelta(hours=POINT_HOURS), "end": now}
    readings: dict[str, list[float]] = {}
    for row in rows(cursor, _WEEKLY_READINGS_SQL, window):
        readings.setdefault(row["id"], []).append(float(row["used_percent"]))
    cost_6h = _account_cost(cursor, window["start"], now)
    pool_points = pool_cost = 0.0
    for ident, entry in zip(ids, entries):
        points = _points_moved(readings.get(ident, []))
        cost = cost_6h.get(ident, 0.0)
        pool_points += points
        pool_cost += cost
        entry["all_models_points_moved_6h"] = round(points, 2)
        entry["cost_usd_6h"] = round(cost, 4)
        entry["usd_per_all_models_point"] = _ratio(cost, points) if points >= MIN_POINTS else None
    usd_per_point = _ratio(pool_cost, pool_points) if pool_points >= MIN_POINTS else None
    cost_1h = sum(_account_cost(cursor, now - timedelta(hours=1), now).values())
    cost_24h = sum(_account_cost(cursor, now - timedelta(hours=24), now).values())
    points_left = sum(entry["weekly_all_points_left"] for entry in entries)
    dollars_left = None if usd_per_point is None else points_left * usd_per_point
    resets = [
        datetime.fromisoformat(entry["weekly_all_resets"].replace("Z", "+00:00"))
        for entry in entries
        if entry["weekly_all_resets"]
    ]
    aware_now = now.replace(tzinfo=timezone.utc)
    upcoming = [reset for reset in resets if reset > aware_now]
    next_reset = min(upcoming) if upcoming else None
    return {
        "usd_per_all_models_point": {
            "hours": POINT_HOURS,
            "pool": usd_per_point,
            "pool_points_moved": round(pool_points, 2),
            "pool_cost_usd": round(pool_cost, 4),
            "min_points": MIN_POINTS,
            "basis": "stored cost_usd on Anthropic accounts over weekly all-models percent points moved",
        },
        "pool_hours_left_last_hour_rate": None if dollars_left is None else _ratio(dollars_left, cost_1h, 2),
        "pool_hours_left_24h_rate": None if dollars_left is None else _ratio(dollars_left, cost_24h / 24, 2),
        "next_weekly_reset": _iso(next_reset) if next_reset else None,
        "next_weekly_reset_hours": round((next_reset - aware_now).total_seconds() / 3600, 2) if next_reset else None,
        "pool_hours_left_basis": {
            "points_left": round(points_left, 2),
            "accounts_with_room": sum(1 for entry in entries if entry["weekly_all_points_left"] > 0),
            "dollars_left": None if dollars_left is None else round(dollars_left, 2),
            "cost_usd_last_60m": round(cost_1h, 4),
            "usd_per_hour_24h": round(cost_24h / 24, 4),
        },
    }
