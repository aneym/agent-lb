from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.usage_export_sql import rows

WINDOW_HOURS = (1, 24)
EDGE_SLACK_MINUTES = 10
MAX_GAP_MINUTES = 60
RESET_SHIFT_SECONDS = 3600
RESET_DROP_POINTS = 5.0
FABLE_QUOTA_KEY = "anthropic_fable_scoped_weekly"

_WEEKLY_SQL = """
SELECT h.account_id AS id, h.recorded_at, h.used_percent, h.reset_at
FROM usage_history h
JOIN accounts a ON a.id = h.account_id AND a.provider = 'anthropic'
WHERE h.window = 'secondary' AND h.recorded_at >= %(since)s AND h.recorded_at <= %(now)s
ORDER BY h.account_id, h.recorded_at
"""

_FABLE_SQL = """
SELECT h.account_id AS id, h.recorded_at, h.used_percent, h.reset_at
FROM additional_usage_history h
JOIN accounts a ON a.id = h.account_id AND a.provider = 'anthropic'
WHERE h.quota_key = %(quota_key)s AND h.recorded_at >= %(since)s AND h.recorded_at <= %(now)s
ORDER BY h.account_id, h.recorded_at
"""

BASIS = (
    "percentage points of the weekly limit used, from the stored limit readings (usage_history window secondary for "
    f"all models, additional_usage_history {FABLE_QUOTA_KEY} for Fable), never from cost. The span runs from the last "
    f"reading at or before the window start (at most {EDGE_SLACK_MINUTES} min before it) to the latest reading (at most "
    f"{EDGE_SLACK_MINUTES} min old), with no gap over {MAX_GAP_MINUTES} min between readings; otherwise the figure is "
    "null and points_coverage says why. A weekly reset inside the span (reset time moving forward over 1 h, or a drop "
    f"over {RESET_DROP_POINTS:g} points, which a redeemed reset also causes) splits it: use before the reset plus use "
    "from the first reading after it, both positive, so the drop itself never counts as use. Readings are whole percents, so a figure is good to about 1 point."
)


def _iso(value: datetime) -> str:
    return value.isoformat() + "Z"


def _is_reset(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    before, after = previous["reset_at"], current["reset_at"]
    if before is not None and after is not None and after - before > RESET_SHIFT_SECONDS:
        return True
    return float(current["used_percent"]) < float(previous["used_percent"]) - RESET_DROP_POINTS


def _measure(readings: list[dict[str, Any]], start: datetime, now: datetime) -> tuple[float | None, dict[str, Any]]:
    anchor = None
    for index, reading in enumerate(readings):
        if reading["recorded_at"] <= start:
            anchor = index
        else:
            break
    if anchor is None or start - readings[anchor]["recorded_at"] > timedelta(minutes=EDGE_SLACK_MINUTES):
        first = next((r["recorded_at"] for r in readings if r["recorded_at"] >= start), None)
        reason = "no reading within %d min before the window start" % EDGE_SLACK_MINUTES
        return None, {"missing": reason + (f"; first reading after it {_iso(first)}" if first else "")}
    span = readings[anchor:]
    latest = span[-1]["recorded_at"]
    if now - latest > timedelta(minutes=EDGE_SLACK_MINUTES):
        return None, {"missing": f"latest reading {_iso(latest)} is over {EDGE_SLACK_MINUTES} min old"}
    moved = 0.0
    resets = 0
    segment_start = float(span[0]["used_percent"])
    for previous, current in zip(span, span[1:]):
        gap = current["recorded_at"] - previous["recorded_at"]
        if gap > timedelta(minutes=MAX_GAP_MINUTES):
            return None, {
                "missing": f"{int(gap.total_seconds() // 60)} min without a reading from {_iso(previous['recorded_at'])}"
            }
        if _is_reset(previous, current):
            moved += max(0.0, float(previous["used_percent"]) - segment_start)
            segment_start = float(current["used_percent"])
            resets += 1
    moved += max(0.0, float(span[-1]["used_percent"]) - segment_start)
    coverage = {"from": _iso(span[0]["recorded_at"]), "to": _iso(latest), "readings": len(span), "resets": resets}
    return round(moved, 2), coverage


def _series(cursor: Any, sql: str, params: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    series: dict[str, list[dict[str, Any]]] = {}
    for row in rows(cursor, sql, params):
        series.setdefault(row["id"], []).append(row)
    return series


def _hours_left(points_left: float, points: float | None, hours: int) -> float | None:
    if points is None or points <= 0:
        return None
    return round(points_left / (points / hours), 2)


def apply(cursor: Any, now: datetime, ids: list[str], entries: list[dict[str, Any]]) -> dict[str, Any]:
    params = {"since": now - timedelta(hours=max(WINDOW_HOURS), minutes=EDGE_SLACK_MINUTES), "now": now}
    limits = {
        "weekly_all": _series(cursor, _WEEKLY_SQL, params),
        "fable": _series(cursor, _FABLE_SQL, {**params, "quota_key": FABLE_QUOTA_KEY}),
    }
    fields = [(f"{name}_points_last_{hours}h", name, hours) for name in limits for hours in WINDOW_HOURS]
    pool: dict[str, float] = {field: 0.0 for field, _, _ in fields}
    missing: dict[str, list[str]] = {field: [] for field, _, _ in fields}
    for ident, entry in zip(ids, entries):
        coverage: dict[str, Any] = {}
        for field, name, hours in fields:
            points, coverage[field] = _measure(limits[name].get(ident, []), now - timedelta(hours=hours), now)
            entry[field] = points
            if points is None:
                missing[field].append(entry["account"])
            else:
                pool[field] += points
        entry["points_coverage"] = coverage
    points_left = round(sum(entry["weekly_all_points_left"] for entry in entries), 2)
    pace = {
        **{field: round(total, 2) for field, total in pool.items()},
        "accounts": len(entries),
        "accounts_counted": {field: len(entries) - len(missing[field]) for field in pool},
        "accounts_without_coverage": missing,
        "points_left": points_left,
        "weekly_all_points_per_hour_last_1h": pool["weekly_all_points_last_1h"],
        "weekly_all_points_per_hour_24h": round(pool["weekly_all_points_last_24h"] / 24, 4),
        "pool_rule": "each pool figure is the sum of the account figures that are not null, over the same Anthropic "
        "accounts as pool_hours_left_basis.points_left; accounts_without_coverage lists the ones left out",
        "basis": BASIS,
    }
    return {
        "points_pace": pace,
        "pool_hours_left_points_last_hour_rate": _hours_left(points_left, pool["weekly_all_points_last_1h"], 1),
        "pool_hours_left_points_24h_rate": _hours_left(points_left, pool["weekly_all_points_last_24h"], 24),
    }
