from __future__ import annotations

import gzip
from datetime import UTC, datetime
from pathlib import Path

from app import retention_archive as archive
from app import retention_policy as policy

SAMPLE_ROWS = 20000


def _sizes(cursor, table: str) -> dict:
    cursor.execute(
        "SELECT pg_relation_size(c.oid), coalesce(pg_total_relation_size(c.reltoastrelid), 0), pg_indexes_size(c.oid),"
        " pg_total_relation_size(c.oid) FROM pg_class c WHERE c.oid = %s::regclass",
        (table,),
    )
    heap, toast, indexes, total = cursor.fetchone()
    return {"heap_bytes": heap, "toast_bytes": toast, "index_bytes": indexes, "total_bytes": total}


def _window(cursor, cutoff: datetime, totals: dict) -> dict:
    cursor.execute(
        "SELECT count(*), count(*) FILTER (WHERE deleted_at IS NOT NULL), coalesce(sum(pg_column_size(r.*)), 0),"
        " coalesce(sum(octet_length(r::text)), 0), count(DISTINCT date(requested_at))"
        " FROM request_logs r WHERE requested_at < %s",
        (cutoff,),
    )
    rows, soft_deleted, row_bytes, text_bytes, days = cursor.fetchone()
    cursor.execute(
        "SELECT count(*), coalesce(sum(pg_column_size(p.*)), 0), coalesce(sum(octet_length(p::text)), 0)"
        " FROM request_context_parts p JOIN request_logs r ON r.id = p.request_log_id WHERE r.requested_at < %s",
        (cutoff,),
    )
    part_rows, part_bytes, part_text = cursor.fetchone()
    cursor.execute(
        "SELECT count(*) FROM (SELECT 1 FROM request_logs WHERE requested_at < %s GROUP BY date(requested_at),"
        " account_id, api_key_id, provider, model, source, request_kind, status, deleted_at IS NOT NULL) g",
        (cutoff,),
    )
    rollup_rows = cursor.fetchone()[0]
    logs, parts = totals["request_logs"], totals["request_context_parts"]
    log_share = rows / logs["rows"] if logs["rows"] else 0.0
    part_share = part_rows / parts["rows"] if parts["rows"] else 0.0
    log_heap = min(logs["heap_bytes"], int(row_bytes + rows * 28))
    part_heap = min(parts["heap_bytes"], int(part_bytes + part_rows * 28))
    return {
        "cutoff": cutoff.isoformat(),
        "days": days,
        "request_logs": {
            "rows": rows,
            "soft_deleted_rows": soft_deleted,
            "share_of_rows": round(log_share, 4),
            "heap_bytes": log_heap,
            "index_bytes": int(logs["index_bytes"] * log_share),
            "csv_bytes": int(text_bytes),
        },
        "request_context_parts": {
            "rows": part_rows,
            "heap_bytes": part_heap,
            "index_bytes": int(parts["index_bytes"] * part_share),
            "csv_bytes": int(part_text),
        },
        "rollup_rows": rollup_rows,
        "freed_bytes_after_rewrite": log_heap
        + int(logs["index_bytes"] * log_share)
        + part_heap
        + int(parts["index_bytes"] * part_share),
    }


def _gzip_ratio(cursor) -> float:
    cursor.execute(
        "SELECT coalesce(string_agg(r::text, E'\\n'), '') FROM (SELECT * FROM request_logs ORDER BY id LIMIT %s) r",
        (SAMPLE_ROWS,),
    )
    sample = cursor.fetchone()[0].encode()
    return len(gzip.compress(sample, 6)) / len(sample) if sample else 0.2


def plan(connection, days: int, archive_dir: Path | None) -> dict:
    with connection.transaction():
        cursor = connection.cursor()
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        cursor.execute("SET LOCAL statement_timeout = '240s'")
        totals = {}
        for table in ("request_logs", "request_context_parts", "request_log_daily"):
            cursor.execute("SELECT to_regclass(%s)", (table,))
            if cursor.fetchone()[0] is None:
                totals[table] = {"absent": True, "rows": 0}
                continue
            totals[table] = _sizes(cursor, table)
            cursor.execute(f"SELECT count(*) FROM {table}")
            totals[table]["rows"] = cursor.fetchone()[0]
        cursor.execute("SELECT current_database(), pg_database_size(current_database())")
        database, database_bytes = cursor.fetchone()
        ratio = _gzip_ratio(cursor)
        windows = {}
        for candidate in sorted({*policy.CANDIDATE_DAYS, days}):
            window = _window(cursor, policy.cutoff_for(candidate), totals)
            csv = window["request_logs"]["csv_bytes"] + window["request_context_parts"]["csv_bytes"]
            window["archive_bytes_estimate"] = int(csv * ratio)
            windows[str(candidate)] = window
    chosen = windows[str(days)]
    target = None
    if archive_dir is not None:
        target = archive.room(archive.off_root(archive_dir), 2 * chosen["archive_bytes_estimate"] + (1 << 30))
    return {
        "schema": 1,
        "mode": "dry-run",
        "generated_at": datetime.now(UTC).isoformat(),
        "database": database,
        "database_bytes": database_bytes,
        "days_kept": days,
        "policy": policy.POLICY,
        "tables": totals,
        "gzip_ratio_sample": round(ratio, 4),
        "windows": windows,
        "chosen": chosen,
        "archive_dir": target,
        "reclaim": policy.RECLAIM,
        "writes": "none; dry-run opened one read-only transaction",
    }
