from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

SCHEMA = "agent-lb-headroom/1"
FRESH_MINUTES = 30
WEEKLY_HEADROOM_PERCENT = 95.0
FABLE_QUOTA_KEY = "anthropic_fable_scoped_weekly"
FABLE_MODEL = "claude-fable-5-2"
FABLE_RATE_HOURS = (1, 6)
_BLOCKED_STATUSES = {"reauth_required", "deactivated", "paused"}
_BLOCKED_REASON = "anthropic_oauth_organization_blocked"

_ACCOUNTS_SQL = """
SELECT id, left(id, 8) AS account, plan_type, status::text AS status, subscription_status, deactivation_reason
FROM accounts
WHERE provider = 'anthropic'
ORDER BY id
"""

_WINDOWS_SQL = """
SELECT a.id, w.name AS window_name, latest.used_percent, latest.reset_at, latest.window_minutes, latest.recorded_at
FROM accounts a
CROSS JOIN (VALUES ('primary'), ('secondary')) AS w(name)
CROSS JOIN LATERAL (
    SELECT h.used_percent, h.reset_at, h.window_minutes, h.recorded_at
    FROM usage_history h
    WHERE h.account_id = a.id AND h.window = w.name AND h.recorded_at >= %(since)s
    ORDER BY h.recorded_at DESC
    LIMIT 1
) AS latest
WHERE a.provider = 'anthropic'
"""

_FABLE_SQL = """
SELECT a.id, latest.used_percent, latest.reset_at, latest.recorded_at
FROM accounts a
CROSS JOIN LATERAL (
    SELECT h.used_percent, h.reset_at, h.recorded_at
    FROM additional_usage_history h
    WHERE h.account_id = a.id AND h.quota_key = %(quota_key)s AND h.recorded_at >= %(since)s
    ORDER BY h.recorded_at DESC
    LIMIT 1
) AS latest
WHERE a.provider = 'anthropic'
"""

_FABLE_COST_SQL = """
SELECT coalesce(sum(cost_usd), 0) AS cost_usd, count(*) AS requests, count(*) FILTER (WHERE cost_usd IS NULL) AS rows_without_cost
FROM request_logs
WHERE deleted_at IS NULL AND model = %(model)s AND requested_at >= %(start)s AND requested_at < %(end)s
"""


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _epoch(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _pct(value: float | None) -> float | None:
    return None if value is None else round(float(value), 2)


def _rows(cursor: Any, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    cursor.execute(sql, params or {})
    columns = [column.name for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _usable(account: dict[str, Any], five_hour: dict[str, Any] | None, weekly: dict[str, Any] | None) -> bool:
    if str(account["status"]).lower() in _BLOCKED_STATUSES:
        return False
    if (account["subscription_status"] or "").strip().lower() == "canceled":
        return False
    if account["deactivation_reason"] == _BLOCKED_REASON:
        return False
    if five_hour is None or weekly is None:
        return False
    return float(five_hour["used_percent"]) < 100.0 and float(weekly["used_percent"]) < 100.0


def _note(account: dict[str, Any], five_hour: dict[str, Any] | None, weekly: dict[str, Any] | None) -> str | None:
    if five_hour is None or weekly is None:
        return f"no usage reading in the last {FRESH_MINUTES} min"
    if str(account["status"]).lower() != "active":
        return f"status {account['status']}"
    return None


def _entry(
    account: dict[str, Any], windows: dict[tuple[str, str], dict[str, Any]], fable: dict[str, Any] | None
) -> dict[str, Any]:
    five_hour = windows.get((account["id"], "primary"))
    weekly = windows.get((account["id"], "secondary"))
    fable_used = _pct(fable["used_percent"]) if fable else None
    recorded = [row["recorded_at"] for row in (five_hour, weekly, fable) if row]
    return {
        "account": account["account"],
        "plan": account["plan_type"],
        "status": account["status"],
        "rate_limit_tier": None,
        "weekly_all_used_pct": _pct(weekly["used_percent"]) if weekly else None,
        "weekly_all_resets": _epoch(weekly["reset_at"]) if weekly else None,
        "fable_weekly_used_pct": fable_used,
        "fable_weekly_headroom_pct": None if fable_used is None else round(100.0 - fable_used, 2),
        "fable_resets": _epoch(fable["reset_at"]) if fable else None,
        "fable_resets_held": None,
        "five_hour_used_pct": _pct(five_hour["used_percent"]) if five_hour else None,
        "five_hour_resets": _epoch(five_hour["reset_at"]) if five_hour else None,
        "limits_recorded_at": _iso(min(recorded)) if recorded else None,
        "usable_now": _usable(account, five_hour, weekly),
        "note": _note(account, five_hour, weekly),
    }


def _summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    read = [entry for entry in entries if entry["weekly_all_used_pct"] is not None]
    fable = [entry["fable_weekly_used_pct"] for entry in entries if entry["fable_weekly_used_pct"] is not None]
    return {
        "accounts": len(entries),
        "with_limits_read": len(read),
        "fable_bucket_used_pct_max": max(fable) if fable else None,
        "weekly_all_under_95pct": [
            entry["account"] for entry in read if entry["weekly_all_used_pct"] < WEEKLY_HEADROOM_PERCENT
        ],
        "usable_now": [entry["account"] for entry in entries if entry["usable_now"]],
    }


def _fable_burn(cursor: Any, now: datetime) -> dict[str, Any]:
    burn: dict[str, Any] = {"model": FABLE_MODEL, "basis": "stored cost_usd of request_logs rows, every status"}
    for hours in FABLE_RATE_HOURS:
        params = {"model": FABLE_MODEL, "start": now - timedelta(hours=hours), "end": now}
        row = _rows(cursor, _FABLE_COST_SQL, params)[0]
        burn[f"last_{hours}h"] = {
            "hours": hours,
            "cost_usd": round(float(row["cost_usd"]), 4),
            "usd_per_hour": round(float(row["cost_usd"]) / hours, 4),
            "requests": int(row["requests"]),
            "rows_without_cost": int(row["rows_without_cost"]),
        }
    return burn


def build(cursor: Any, now: datetime, source: str, generated_at: str) -> dict[str, Any]:
    since = now - timedelta(minutes=FRESH_MINUTES)
    accounts = _rows(cursor, _ACCOUNTS_SQL)
    windows = {(row["id"], row["window_name"]): row for row in _rows(cursor, _WINDOWS_SQL, {"since": since})}
    fable = {row["id"]: row for row in _rows(cursor, _FABLE_SQL, {"since": since, "quota_key": FABLE_QUOTA_KEY})}
    entries = [_entry(account, windows, fable.get(account["id"])) for account in accounts]
    return {
        "schema": SCHEMA,
        "generated_at": generated_at,
        "source": source,
        "window": {
            "kind": "latest reading per account",
            "fresh_minutes": FRESH_MINUTES,
            "as_of_utc": _iso(now),
            "five_hour": "usage_history window primary (300 min)",
            "weekly_all": "usage_history window secondary (10080 min)",
            "fable_weekly": f"additional_usage_history quota_key {FABLE_QUOTA_KEY}",
        },
        "usable_rule": "status not reauth_required, deactivated or paused; subscription not canceled; "
        f"both windows read in the last {FRESH_MINUTES} min; 5-hour and weekly_all under 100%",
        "fable_usd_per_hour": _fable_burn(cursor, now),
        "accounts_now": entries,
        "pool_summary": _summary(entries),
    }
