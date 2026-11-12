from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.usage_export_sql import rows

SCHEMA = "agent-lb-codex/1"
FRESH_MINUTES = 30
LOOKBACK_DAYS = 30
FIVE_HOUR_MINUTES = 300
WEEKLY_MINUTES = 10080
_UNREADABLE_STATUSES = {"reauth_required", "deactivated", "paused"}

_ACCOUNTS_SQL = """
SELECT id, left(id, 8) AS account, plan_type, status::text AS status, owner_instance
FROM accounts
WHERE provider = 'openai'
ORDER BY id
"""

_WINDOWS_SQL = """
SELECT DISTINCT ON (h.account_id, h.window_minutes)
       h.account_id AS id, h.window_minutes, h.used_percent, h.reset_at, h.recorded_at
FROM usage_history h
JOIN accounts a ON a.id = h.account_id
WHERE a.provider = 'openai' AND h.window_minutes IN (%(five)s, %(week)s) AND h.recorded_at >= %(since)s
ORDER BY h.account_id, h.window_minutes, h.recorded_at DESC
"""

RULE = (
    "one entry per OpenAI (Codex) account in agent-lb, read from the latest usage_history reading per window in the "
    f"last {LOOKBACK_DAYS} days: the 300-minute window is five_hour, the 10080-minute window is weekly, whatever "
    "window name agent-lb stored it under. Readings come from the rate-limit headers and usage endpoint agent-lb already "
    "polls; nothing extra is called. state is fresh when the account can be served and its weekly reading is at most "
    f"{FRESH_MINUTES} min old, stale when the last reading is older or the account needs a sign-in (the figures are "
    "then the last known ones), and unknown when no weekly reading exists. OpenAI reports no 5-hour window for Pro "
    "accounts, so five_hour_* is null there."
)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc, microsecond=0).isoformat().replace("+00:00", "Z")


def _epoch(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _pct(row: dict[str, Any] | None) -> float | None:
    return None if row is None else round(float(row["used_percent"]), 2)


def _state(account: dict[str, Any], weekly: dict[str, Any] | None, now: datetime) -> tuple[str, list[str]]:
    notes: list[str] = []
    status = str(account["status"]).lower()
    if status in _UNREADABLE_STATUSES:
        notes.append(f"status {status}: agent-lb cannot read its limits until it is signed in again")
    if weekly is None:
        notes.append(f"no weekly reading in the last {LOOKBACK_DAYS} days")
        return "unknown", notes
    age = int((now - weekly["recorded_at"]).total_seconds() // 60)
    if age > FRESH_MINUTES:
        notes.append(f"weekly reading {age} min old")
    if weekly["reset_at"] is not None and weekly["reset_at"] <= now.replace(tzinfo=timezone.utc).timestamp():
        notes.append("its weekly reset time has passed since this reading, so the week has turned over")
    return ("stale" if notes else "fresh"), notes


def _entry(account: dict[str, Any], windows: dict[tuple[str, int], dict[str, Any]], now: datetime) -> dict[str, Any]:
    five_hour = windows.get((account["id"], FIVE_HOUR_MINUTES))
    weekly = windows.get((account["id"], WEEKLY_MINUTES))
    state, notes = _state(account, weekly, now)
    if five_hour is None and weekly is not None:
        notes.append("no 5-hour window: OpenAI reports only a weekly window for this plan")
    read = [row["recorded_at"] for row in (five_hour, weekly) if row]
    return {
        "account": account["account"],
        "plan": account["plan_type"],
        "owner": "teammate-owned" if account["owner_instance"] else "primary",
        "status": account["status"],
        "weekly_used_pct": _pct(weekly),
        "weekly_resets_at": _epoch(weekly["reset_at"]) if weekly else None,
        "five_hour_used_pct": _pct(five_hour),
        "five_hour_resets_at": _epoch(five_hour["reset_at"]) if five_hour else None,
        "read_at": _iso(max(read)) if read else None,
        "state": state,
        "stale": state != "fresh",
        "note": "; ".join(notes) or None,
    }


def build(cursor: Any, now: datetime, source: str, generated_at: str) -> dict[str, Any]:
    since = now - timedelta(days=LOOKBACK_DAYS)
    params = {"five": FIVE_HOUR_MINUTES, "week": WEEKLY_MINUTES, "since": since}
    windows = {(row["id"], row["window_minutes"]): row for row in rows(cursor, _WINDOWS_SQL, params)}
    entries = [_entry(account, windows, now) for account in rows(cursor, _ACCOUNTS_SQL)]
    return {
        "schema": SCHEMA,
        "generated_at": generated_at,
        "source": source,
        "window": {"as_of_utc": _iso(now), "lookback_days": LOOKBACK_DAYS, "stale_minutes": FRESH_MINUTES},
        "rule": RULE,
        "plans": entries,
    }
