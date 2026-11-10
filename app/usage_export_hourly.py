from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.usage_export_sql import rows

SCHEMA = "agent-lb-usage-hourly/1"
HOURS = 48
SUMMARY_HOURS = 24

METRICS = (
    "requests",
    "ok_requests",
    "failed_requests",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cache_write_5m_tokens",
    "cache_write_1h_tokens",
    "cost_usd",
    "rows_without_cost",
)

SUMS = """
    count(*) AS requests,
    count(*) FILTER (WHERE status = 'success') AS ok_requests,
    count(*) FILTER (WHERE status <> 'success') AS failed_requests,
    coalesce(sum(input_tokens), 0) AS input_tokens,
    coalesce(sum(output_tokens), 0) AS output_tokens,
    coalesce(sum(coalesce(cache_read_tokens, cached_input_tokens)), 0) AS cache_read_tokens,
    coalesce(sum(cache_creation_tokens), 0) AS cache_write_tokens,
    coalesce(sum(cache_creation_tokens) FILTER (WHERE cache_creation_tier IS DISTINCT FROM '1h'), 0)
        AS cache_write_5m_tokens,
    coalesce(sum(cache_creation_tokens) FILTER (WHERE cache_creation_tier = '1h'), 0) AS cache_write_1h_tokens,
    coalesce(sum(cost_usd), 0) AS cost_usd,
    count(*) FILTER (WHERE cost_usd IS NULL) AS rows_without_cost
"""

WINDOW = "deleted_at IS NULL AND requested_at >= %(start)s AND requested_at < %(end)s"

_BUCKETS_SQL = f"""
SELECT date_trunc('hour', requested_at) AS hour, provider, model, client_session_id,
       left(account_id, 8) AS account, {SUMS}
FROM request_logs
WHERE {WINDOW}
GROUP BY 1, 2, 3, 4, 5
ORDER BY 1, 2, 3, 5, 4
"""

_BY_MODEL_SQL = f"""
SELECT provider, model, {SUMS}, min(requested_at) AS first_at, max(requested_at) AS last_at
FROM request_logs
WHERE {WINDOW}
GROUP BY 1, 2
ORDER BY cost_usd DESC, requests DESC
"""

NOTES = [
    "cost_usd is the cost agent-lb stored on each row at request time (authoritative ledger value).",
    "cache_read_tokens = coalesce(cache_read_tokens, cached_input_tokens); cache_write_tokens = cache_creation_tokens; "
    "5m vs 1h split from cache_creation_tier (null counted as 5m).",
    "ok_requests counts status success; failed_requests counts every other status.",
    "account is the first 8 characters of the agent-lb account id; client_session_id is the client's own session id.",
    "hour is the UTC hour the request started; the first and last buckets are partial.",
    "by_model_24h and totals_24h cover exactly the last 24 hours up to window.end_utc; by_model and totals cover the 48 hours.",
]


def iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def metrics(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {name: int(row[name]) for name in METRICS if name != "cost_usd"}
    out["cost_usd"] = round(float(row["cost_usd"]), 6)
    return {name: out[name] for name in METRICS}


def _by_model(cursor: Any, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "provider": row["provider"],
            "model": row["model"],
            **metrics(row),
            "first_at": iso(row["first_at"]),
            "last_at": iso(row["last_at"]),
        }
        for row in rows(cursor, _BY_MODEL_SQL, params)
    ]


def _totals(models: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, Any] = {name: sum(model[name] for model in models) for name in METRICS}
    totals["cost_usd"] = round(totals["cost_usd"], 4)
    return totals


def build(cursor: Any, end: datetime, source: str, generated_at: str) -> dict[str, Any]:
    start = end - timedelta(hours=HOURS)
    window = {"start": start, "end": end}
    summary_window = {"start": end - timedelta(hours=SUMMARY_HOURS), "end": end}
    buckets = [
        {
            "hour": row["hour"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "provider": row["provider"],
            "model": row["model"],
            "client_session_id": row["client_session_id"],
            "account": row["account"],
            **metrics(row),
        }
        for row in rows(cursor, _BUCKETS_SQL, window)
    ]
    by_model = _by_model(cursor, window)
    by_model_24h = _by_model(cursor, summary_window)
    return {
        "schema": SCHEMA,
        "source": source,
        "window": {
            "hours": HOURS,
            "start_utc": iso(start),
            "end_utc": iso(end),
            "bucket": "hour",
            "clock": "DB now() at time zone utc",
        },
        "generated_at": generated_at,
        "notes": NOTES,
        "bucket_keys": ["hour", "provider", "model", "client_session_id", "account", *METRICS],
        "totals": _totals(by_model),
        "by_model": by_model,
        "totals_24h": _totals(by_model_24h),
        "by_model_24h": by_model_24h,
        "buckets": buckets,
    }
