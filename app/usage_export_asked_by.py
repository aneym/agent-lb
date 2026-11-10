from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.usage_export_asked_by_records import OPEN_TURN_HOURS, OWNER, Turn
from app.usage_export_hourly import HOURS, METRICS, SUMS, WINDOW, metrics
from app.usage_export_sql import rows

_SQL = f"""
WITH turns AS (
    SELECT * FROM unnest(%(t_session)s::text[], %(t_asker)s::text[], %(t_start)s::timestamp[], %(t_end)s::timestamp[])
        AS t(session, asker, start_at, end_at)
), tagged AS (
    SELECT r.*,
        CASE WHEN r.client_session_id = ANY(%(t_session)s::text[]) THEN (
            SELECT t.asker FROM turns t
            WHERE t.session = r.client_session_id AND r.requested_at >= t.start_at AND r.requested_at < t.end_at
            ORDER BY t.start_at DESC
            LIMIT 1
        ) END AS asker
    FROM request_logs r
    WHERE {WINDOW}
)
SELECT date_trunc('hour', requested_at) AS hour, coalesce(asker, '{OWNER}') AS asker, provider, model, {SUMS}
FROM tagged
GROUP BY 1, 2, 3, 4
ORDER BY 1, 2, 3, 4
"""

RULE = (
    "A request belongs to an asker when its client_session_id equals a turn record's session and requested_at falls in "
    "[start, end) of that turn; when turns of one session overlap, the turn that started last wins. Every other request "
    f"is under '{OWNER}'. A turn with end null counts as running until window.end_utc, at most {OPEN_TURN_HOURS} hours "
    "after its start. For each turn id the last record read wins. Asker ids are opaque Rails account ids."
)


def build(cursor: Any, end: datetime, turns: list[Turn], records: dict[str, Any]) -> dict[str, Any]:
    start = end - timedelta(hours=HOURS)
    params = {
        "start": start,
        "end": end,
        "t_session": [turn.session for turn in turns],
        "t_asker": [turn.asker for turn in turns],
        "t_start": [turn.start for turn in turns],
        "t_end": [turn.end for turn in turns],
    }
    buckets = [
        {
            "hour": row["hour"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "asker": row["asker"],
            "provider": row["provider"],
            "model": row["model"],
            **metrics(row),
        }
        for row in rows(cursor, _SQL, params)
    ]
    totals: dict[str, dict[str, Any]] = {}
    for bucket in buckets:
        total = totals.setdefault(bucket["asker"], {name: 0 for name in METRICS})
        for name in METRICS:
            total[name] += bucket[name]
    for total in totals.values():
        total["cost_usd"] = round(total["cost_usd"], 4)
    return {
        "rule": RULE,
        "records": records,
        "bucket_keys": ["hour", "asker", "provider", "model", *METRICS],
        "totals": [
            {"asker": asker, **total}
            for asker, total in sorted(totals.items(), key=lambda item: (item[0] != OWNER, -item[1]["cost_usd"]))
        ],
        "buckets": buckets,
    }
