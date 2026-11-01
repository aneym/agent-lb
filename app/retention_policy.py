from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

REQUEST_LOG_DAYS = 90
CANDIDATE_DAYS = (30, 60, 90)
LOCK_KEY = "agent-lb-retention"
WARMUP_KINDS = "('warmup', 'limit_warmup')"

POLICY = {
    "request_logs": f"raw rows {REQUEST_LOG_DAYS} days, then archived and folded into request_log_daily",
    "request_context_parts": "follows its request_logs row (ON DELETE CASCADE), archived with it",
    "request_log_daily": "kept; one row per day and dimension set, read for lifetime and long-range totals",
    "usage_history": "kept; readers need the latest row per account and window, reclaim by index rebuild",
    "additional_usage_history": "kept; readers need the latest row per quota key, reclaim by index rebuild",
}

RECLAIM = {
    "vacuum": "plain VACUUM (autovacuum) makes freed pages reusable inside Postgres, no blocking lock, no OS space",
    "vacuum_full": "VACUUM FULL request_logs rewrites table and indexes under ACCESS EXCLUSIVE: all reads and"
    " writes to request_logs wait for the whole run; needs free disk equal to the kept table plus indexes",
    "pg_repack": "pg_repack -t request_logs rebuilds online, ACCESS EXCLUSIVE only for a moment at start and"
    " end; needs the extension installed and the same free disk",
    "reindex": "REINDEX TABLE CONCURRENTLY rebuilds bloated indexes without blocking writes",
}


@dataclass(frozen=True)
class Day:
    start: datetime
    end: datetime

    @property
    def label(self) -> str:
        return self.start.date().isoformat()


def cutoff_for(days: int, now: datetime | None = None) -> datetime:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    midnight = datetime.combine(current.date(), datetime.min.time())
    return midnight - timedelta(days=days)


def day_of(value: date) -> Day:
    start = datetime.combine(value, datetime.min.time())
    return Day(start, start + timedelta(days=1))


def quoted(names: list[str], alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return ", ".join(f'{prefix}"{name}"' for name in names)


def table_columns(cursor, table: str) -> list[str]:
    cursor.execute(
        "SELECT attname FROM pg_attribute WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped"
        " ORDER BY attnum",
        (table,),
    )
    return [row[0] for row in cursor.fetchall()]


LOG_RANGE = "r.requested_at >= %(start)s AND r.requested_at < %(end)s AND r.id <= %(max_id)s"


def log_select(columns: list[str]) -> str:
    return f"SELECT {quoted(columns, 'r')} FROM request_logs r WHERE {LOG_RANGE} ORDER BY r.id"


def part_select(columns: list[str]) -> str:
    return (
        f"SELECT {quoted(columns, 'p')} FROM request_context_parts p JOIN request_logs r ON r.id = p.request_log_id"
        f" WHERE {LOG_RANGE} ORDER BY p.request_log_id"
    )


def log_checksum(columns: list[str]) -> str:
    return (
        f"SELECT count(*), md5(coalesce(string_agg(ROW({quoted(columns, 'r')})::text, E'\\n' ORDER BY r.id), ''))"
        f" FROM request_logs r WHERE {LOG_RANGE}"
    )


def part_checksum(columns: list[str]) -> str:
    return (
        f"SELECT count(*), md5(coalesce(string_agg(ROW({quoted(columns, 'p')})::text, E'\\n'"
        " ORDER BY p.request_log_id), ''))"
        f" FROM request_context_parts p JOIN request_logs r ON r.id = p.request_log_id WHERE {LOG_RANGE}"
    )


ROLLUP_INSERT = f"""
INSERT INTO request_log_daily (
    archive_id, day, account_id, api_key_id, provider, model, source, request_kind, status, deleted, superseded,
    requests, input_tokens, output_tokens, output_or_reasoning_tokens, cached_input_tokens, cache_creation_tokens,
    cache_read_tokens, reasoning_tokens, cost_usd, latency_ms_sum, latency_count
)
SELECT %(archive_id)s, date(r.requested_at), r.account_id, r.api_key_id, r.provider, r.model, r.source,
    r.request_kind, r.status, r.deleted_at IS NOT NULL,
    (r.request_kind NOT IN {WARMUP_KINDS} AND r.deleted_at IS NULL AND EXISTS (
        SELECT 1 FROM request_logs n
        WHERE n.account_id = r.account_id AND n.request_id = r.request_id AND n.requested_at = r.requested_at
            AND n.id > r.id AND n.request_kind NOT IN {WARMUP_KINDS} AND n.deleted_at IS NULL
    )),
    count(*), coalesce(sum(r.input_tokens), 0), coalesce(sum(r.output_tokens), 0),
    coalesce(sum(coalesce(r.output_tokens, r.reasoning_tokens, 0)), 0), coalesce(sum(r.cached_input_tokens), 0),
    coalesce(sum(r.cache_creation_tokens), 0), coalesce(sum(r.cache_read_tokens), 0),
    coalesce(sum(r.reasoning_tokens), 0), coalesce(sum(r.cost_usd), 0), coalesce(sum(r.latency_ms), 0),
    count(r.latency_ms)
FROM request_logs r WHERE {LOG_RANGE}
GROUP BY 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
"""

LOG_DELETE = f"DELETE FROM request_logs r WHERE {LOG_RANGE}"
LOG_LOCK = f"SELECT r.id FROM request_logs r WHERE {LOG_RANGE} FOR UPDATE"
