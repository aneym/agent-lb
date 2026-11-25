from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.usage_export_sql import rows

_EFFORT_SQL = """
SELECT model, coalesce(nullif(reasoning_effort, ''), 'unset') AS effort, count(*) AS requests
FROM request_logs
WHERE deleted_at IS NULL AND requested_at > %(since)s AND requested_at <= %(now)s
GROUP BY 1, 2
ORDER BY 3 DESC
"""


def build(cursor: Any, now: datetime) -> dict[str, Any]:
    found = rows(cursor, _EFFORT_SQL, {"since": now - timedelta(hours=24), "now": now})
    return {
        "hours": 24,
        "rows": [{"model": row["model"], "effort": row["effort"], "requests": int(row["requests"])} for row in found],
    }
