from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app import usage_export_burn, usage_export_owners, usage_export_points, usage_export_resets
from app.usage_export_sql import rows

SCHEMA = "agent-lb-headroom/2"
FRESH_MINUTES = 30
LOOKBACK_HOURS = 24
WEEKLY_HEADROOM_PERCENT = 95.0
FABLE_QUOTA_KEY = "anthropic_fable_scoped_weekly"
_BLOCKED_STATUSES = {"reauth_required", "deactivated", "paused"}
_WAITING_STATUSES = {"rate_limited", "quota_exceeded"}
_BLOCKED_REASON = "anthropic_oauth_organization_blocked"

_ACCOUNTS_SQL = """
SELECT id, left(id, 8) AS account, status::text AS status, subscription_status, deactivation_reason,
       reset_at, owner_instance, access_expires_at
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


def _eligible(account: dict[str, Any]) -> bool:
    if str(account["status"]).lower() in _BLOCKED_STATUSES:
        return False
    if (account["subscription_status"] or "").strip().lower() == "canceled":
        return False
    return account["deactivation_reason"] != _BLOCKED_REASON


def _points_left(account: dict[str, Any], weekly: dict[str, Any] | None) -> float:
    if weekly is None or not _eligible(account):
        return 0.0
    return round(max(0.0, 100.0 - float(weekly["used_percent"])), 2)


def _points_used(account: dict[str, Any], weekly: dict[str, Any] | None) -> float:
    if weekly is None or not _eligible(account):
        return 0.0
    return round(min(100.0, max(0.0, float(weekly["used_percent"]))), 2)


def _token_expired(account: dict[str, Any], now: datetime) -> bool:
    expires = account["access_expires_at"]
    return expires is not None and expires <= now


def _blocker(account: dict[str, Any], now: datetime) -> str | None:
    status = str(account["status"]).lower()
    if not _eligible(account):
        return f"status {status}" if status in _BLOCKED_STATUSES else "subscription not usable"
    if status == "exchange_uncertain" and _token_expired(account, now):
        return "token exchange uncertain and the access token has expired"
    if account["owner_instance"] and _token_expired(account, now):
        return f"teammate-owned (instance {account['owner_instance']}) with an expired access token"
    if status in _WAITING_STATUSES:
        reset = account["reset_at"]
        if reset is None or now.replace(tzinfo=timezone.utc).timestamp() < reset:
            return f"status {status} until {_epoch(reset) or 'unknown'}"
    return None


def _exhausted(five_hour: dict[str, Any] | None, weekly: dict[str, Any] | None) -> str | None:
    primary = float(five_hour["used_percent"]) if five_hour else 0.0
    secondary = float(weekly["used_percent"]) if weekly else primary
    if primary >= 100.0:
        return "5-hour window at 100%"
    if secondary >= 100.0:
        return "weekly all-models at 100%"
    return None


def _age_minutes(row: dict[str, Any], now: datetime) -> int:
    return int((now - row["recorded_at"]).total_seconds() // 60)


def _notes(
    account: dict[str, Any],
    five_hour: dict[str, Any] | None,
    weekly: dict[str, Any] | None,
    blocker: str | None,
    exhausted: str | None,
    now: datetime,
) -> str | None:
    notes = [text for text in (blocker, exhausted) if text]
    if weekly is None:
        notes.append(f"no weekly reading in the last {LOOKBACK_HOURS} h")
    elif _age_minutes(weekly, now) > FRESH_MINUTES:
        notes.append(f"weekly reading {_age_minutes(weekly, now)} min old")
    if five_hour is None:
        notes.append("five-hour reading absent: Anthropic reports no 5-hour window for this account (7d only)")
    if account["owner_instance"]:
        notes.append(
            f"teammate-owned: its agent-lb instance {account['owner_instance']} keeps the sign-in and pushes the access "
            "token here; only that owner can refresh it or redeem its resets"
        )
    return "; ".join(notes) or None


def _entry(
    account: dict[str, Any],
    windows: dict[tuple[str, str], dict[str, Any]],
    fable: dict[str, Any] | None,
    resets: dict[str, int | None],
    now: datetime,
) -> dict[str, Any]:
    five_hour = windows.get((account["id"], "primary"))
    weekly = windows.get((account["id"], "secondary"))
    fable_used = _pct(fable["used_percent"]) if fable else None
    recorded = [row["recorded_at"] for row in (five_hour, weekly, fable) if row]
    blocker = _blocker(account, now)
    exhausted = _exhausted(five_hour, weekly)
    return {
        "account": account["account"],
        "owner": "teammate-owned" if account["owner_instance"] else "primary",
        "owner_instance": account["owner_instance"],
        "status": account["status"],
        "weekly_all_used_pct": _pct(weekly["used_percent"]) if weekly else None,
        "weekly_all_resets": _epoch(weekly["reset_at"]) if weekly else None,
        "fable_weekly_used_pct": fable_used,
        "fable_weekly_headroom_pct": None if fable_used is None else round(100.0 - fable_used, 2),
        "fable_resets": _epoch(fable["reset_at"]) if fable else None,
        "resets_held": resets.get(account["id"]),
        "five_hour_used_pct": _pct(five_hour["used_percent"]) if five_hour else None,
        "five_hour_resets": _epoch(five_hour["reset_at"]) if five_hour else None,
        "limits_recorded_at": _iso(min(recorded)) if recorded else None,
        "weekly_all_points_left": _points_left(account, weekly),
        "weekly_all_points_used": _points_used(account, weekly),
        "usable_now": blocker is None and exhausted is None,
        "note": _notes(account, five_hour, weekly, blocker, exhausted, now),
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
        "teammate_owned": [entry["account"] for entry in entries if entry["owner"] == "teammate-owned"],
    }


def build(cursor: Any, now: datetime, source: str, generated_at: str) -> dict[str, Any]:
    since = now - timedelta(hours=LOOKBACK_HOURS)
    accounts = rows(cursor, _ACCOUNTS_SQL)
    resets, resets_error = usage_export_resets.held()
    windows = {(row["id"], row["window_name"]): row for row in rows(cursor, _WINDOWS_SQL, {"since": since})}
    fable = {row["id"]: row for row in rows(cursor, _FABLE_SQL, {"since": since, "quota_key": FABLE_QUOTA_KEY})}
    entries = [_entry(account, windows, fable.get(account["id"]), resets, now) for account in accounts]
    ids = [account["id"] for account in accounts]
    burn = usage_export_burn.apply(cursor, now, ids, entries)
    points = usage_export_points.apply(cursor, now, ids, entries)
    owners = usage_export_owners.apply(entries, now)
    return {
        "schema": SCHEMA,
        "generated_at": generated_at,
        "source": source,
        "window": {
            "kind": "latest reading per account",
            "lookback_hours": LOOKBACK_HOURS,
            "stale_note_minutes": FRESH_MINUTES,
            "as_of_utc": _iso(now),
            "five_hour": "usage_history window primary (300 min)",
            "weekly_all": "usage_history window secondary (10080 min)",
            "fable_weekly": f"additional_usage_history quota_key {FABLE_QUOTA_KEY}",
        },
        "usable_rule": "the router's own rule from stored state: status not reauth_required, deactivated or paused; "
        "subscription not canceled or org-blocked; rate_limited or quota_exceeded only once accounts.reset_at has "
        "passed; a mirrored or exchange-uncertain account needs an unexpired access token; latest 5-hour and weekly "
        "readings under 100% (a missing 5-hour reading counts as 0%, a missing weekly reading falls back to the "
        "5-hour one). In-memory error cooldowns are not stored and not shown.",
        "resets_held_source": usage_export_resets.SOURCE
        if resets_error is None
        else f"unavailable this run ({resets_error})",
        "fable_usd_per_hour": usage_export_burn.fable_usd_per_hour(cursor, now),
        **burn,
        **points,
        **owners,
        "accounts_now": entries,
        "pool_summary": _summary(entries),
    }
