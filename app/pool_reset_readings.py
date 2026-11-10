from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from app import usage_export_headroom
from app.core.config.service_database import LOCAL_DATABASE_URL, primary_database_url
from app.pool_reset_rule import Redemption
from app.usage_export_sql import rows

CONFIRM_MINUTES = 30

_OWNED_SQL = """
SELECT left(id, 8) AS account, id
FROM accounts
WHERE provider = 'anthropic' AND (owner_instance IS NULL OR owner_instance = %(local)s)
"""

_ATTEMPTS_SQL = """
SELECT left(r.account_id, 8) AS account, r.state,
       COALESCE(r.applied_at, r.created_at) AT TIME ZONE current_setting('TimeZone') AS at_utc
FROM reset_credit_attempts r
JOIN accounts a ON a.id = r.account_id AND a.provider = 'anthropic'
WHERE (r.applied_at IS NOT NULL OR r.state = 'pending')
  AND COALESCE(r.applied_at, r.created_at) >= %(since)s::timestamptz - interval '1 day'
"""

_WEEKLY_SQL = """
SELECT used_percent, recorded_at FROM usage_history
WHERE account_id LIKE %(prefix)s AND "window" = 'secondary' AND {span}
ORDER BY {order} LIMIT 1
"""

_FABLE_SQL = """
SELECT used_percent, recorded_at FROM additional_usage_history
WHERE account_id LIKE %(prefix)s AND quota_key = 'anthropic_fable_scoped_weekly' AND {span}
ORDER BY {order} LIMIT 1
"""


def _plain(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg://", "postgresql://")


def connect() -> Any:
    import psycopg

    url = os.environ.get("AGENT_LB_DATABASE_URL") or primary_database_url() or LOCAL_DATABASE_URL
    connection = psycopg.connect(
        _plain(url),
        connect_timeout=15,
        options="-c default_transaction_read_only=on -c statement_timeout=120000",
        autocommit=True,
    )
    connection.read_only = True
    return connection


def db_now(cursor: Any) -> datetime:
    row = cursor.execute("SELECT now() AT TIME ZONE 'UTC'").fetchone()
    return row[0]


def headroom(cursor: Any) -> dict[str, Any]:
    now = db_now(cursor)
    stamp = now.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return usage_export_headroom.build(cursor, now, "agent-lb primary Postgres, read by app.pool_reset_auto", stamp)


def owned(cursor: Any) -> dict[str, str]:
    local = os.environ.get("AGENT_LB_LOCAL_INSTANCE_ID", "")
    return {row["account"]: row["id"] for row in rows(cursor, _OWNED_SQL, {"local": local})}


def around(cursor: Any, account: str, at: datetime) -> dict[str, Any]:
    naive = at.astimezone(timezone.utc).replace(tzinfo=None)
    params = {"prefix": account + "%", "at": naive, "until": naive + timedelta(minutes=CONFIRM_MINUTES)}
    result: dict[str, Any] = {}
    for name, sql in (("weekly_all", _WEEKLY_SQL), ("fable", _FABLE_SQL)):
        before = rows(cursor, sql.format(span="recorded_at <= %(at)s", order="recorded_at DESC"), params)
        span = "recorded_at > %(at)s AND recorded_at <= %(until)s"
        after = rows(cursor, sql.format(span=span, order="used_percent ASC, recorded_at ASC"), params)
        result[name] = {
            "before": _reading(before),
            "after": _reading(after),
        }
    return result


def _reading(found: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not found:
        return None
    row = found[0]
    return {"used_pct": float(row["used_percent"]), "recorded_at": row["recorded_at"].isoformat() + "Z"}


def dropped(readings: dict[str, Any]) -> bool:
    for window in readings.values():
        before, after = window["before"], window["after"]
        if before is not None and after is not None and after["used_pct"] < before["used_pct"]:
            return True
    return False


def redemptions(cursor: Any, since: datetime) -> list[Redemption]:
    found = []
    for row in rows(cursor, _ATTEMPTS_SQL, {"since": since}):
        at = row["at_utc"].replace(tzinfo=timezone.utc)
        confirmed = row["state"] != "pending" and dropped(around(cursor, row["account"], at))
        found.append(Redemption(account=row["account"], at=at, confirmed=confirmed))
    return found
