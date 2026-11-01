from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app import retention_archive as archive
from app import retention_policy as policy

TABLES = ("request_logs", "request_context_parts")


def _params(day: policy.Day, max_id: int, archive_id: str = "") -> dict:
    return {"start": day.start, "end": day.end, "max_id": max_id, "archive_id": archive_id}


def export(connection, root: Path, days: int, approval: Path) -> dict:
    cutoff = policy.cutoff_for(days)
    archive_id = f"rl-{cutoff.date().isoformat()}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    target = root / archive_id
    target.mkdir(parents=True, exist_ok=False)
    with connection.transaction():
        cursor = connection.cursor()
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        columns = {table: policy.table_columns(cursor, table) for table in TABLES}
        cursor.execute("SELECT max(id) FROM request_logs WHERE requested_at < %s", (cutoff,))
        max_id = cursor.fetchone()[0] or 0
        cursor.execute(
            "SELECT DISTINCT date(requested_at) FROM request_logs WHERE requested_at < %s AND id <= %s ORDER BY 1",
            (cutoff, max_id),
        )
        day_list = [policy.day_of(row[0]) for row in cursor.fetchall()]
        entries: list[dict] = []
        for day in day_list:
            params = _params(day, max_id)
            cursor.execute(policy.log_checksum(columns["request_logs"]), params)
            rows, digest = cursor.fetchone()
            cursor.execute(policy.part_checksum(columns["request_context_parts"]), params)
            part_rows, part_digest = cursor.fetchone()
            files = {
                "request_logs": archive.copy_out(
                    cursor,
                    policy.log_select(columns["request_logs"]),
                    params,
                    target,
                    f"request_logs/{day.label}.csv.gz",
                ),
                "request_context_parts": archive.copy_out(
                    cursor,
                    policy.part_select(columns["request_context_parts"]),
                    params,
                    target,
                    f"request_context_parts/{day.label}.csv.gz",
                ),
            }
            entries.append(
                {
                    "day": day.label,
                    "rows": rows,
                    "md5": digest,
                    "parts_rows": part_rows,
                    "parts_md5": part_digest,
                    "files": files,
                    "applied_at": None,
                }
            )
    manifest = {
        "schema": 1,
        "archive_id": archive_id,
        "created_at": datetime.now(UTC).isoformat(),
        "days_kept": days,
        "cutoff": cutoff.isoformat(),
        "max_id": max_id,
        "columns": columns,
        "policy": policy.POLICY,
        "approval": {"path": str(approval), "sha256": archive.file_sha256(approval)},
        "days": entries,
        "rows": sum(entry["rows"] for entry in entries),
        "parts_rows": sum(entry["parts_rows"] for entry in entries),
        "archive_bytes": sum(f["bytes"] for entry in entries for f in entry["files"].values()),
    }
    archive.write_json(target / "manifest.json", manifest)
    archive.sha256_sums(target, [f for entry in entries for f in entry["files"].values()])
    return manifest


def verify(root: Path, manifest: dict) -> None:
    for entry in manifest["days"]:
        for item in entry["files"].values():
            archive.verify_file(root, item)


def delete_day(connection, manifest: dict, entry: dict) -> dict:
    day = policy.day_of(datetime.fromisoformat(entry["day"]).date())
    params = _params(day, manifest["max_id"], manifest["archive_id"])
    columns = manifest["columns"]
    with connection.transaction():
        cursor = connection.cursor()
        cursor.execute("SET LOCAL lock_timeout = '5s'")
        cursor.execute("SET LOCAL statement_timeout = '120s'")
        cursor.execute(policy.LOG_LOCK, params)
        cursor.execute(policy.log_checksum(columns["request_logs"]), params)
        live = cursor.fetchone()
        cursor.execute(policy.part_checksum(columns["request_context_parts"]), params)
        parts = cursor.fetchone()
        if tuple(live) != (entry["rows"], entry["md5"]) or tuple(parts) != (entry["parts_rows"], entry["parts_md5"]):
            raise archive.ArchiveError(f"{entry['day']} changed since it was archived; nothing deleted for it")
        cursor.execute(policy.ROLLUP_INSERT, params)
        rollup_rows = cursor.rowcount
        cursor.execute(policy.LOG_DELETE, params)
        if cursor.rowcount != entry["rows"]:
            raise archive.ArchiveError(f"{entry['day']} deleted {cursor.rowcount} rows, expected {entry['rows']}")
    return {
        "day": entry["day"],
        "deleted": entry["rows"],
        "parts_deleted": entry["parts_rows"],
        "rollup_rows": rollup_rows,
    }


def apply(connection, root: Path, days: int, approval: Path) -> dict:
    manifest = export(connection, root, days, approval)
    target = root / manifest["archive_id"]
    verify(target, manifest)
    results = []
    for entry in manifest["days"]:
        results.append(delete_day(connection, manifest, entry))
        entry["applied_at"] = datetime.now(UTC).isoformat()
        archive.write_json(target / "manifest.json", manifest)
    connection.execute("ANALYZE request_logs")
    connection.execute("ANALYZE request_context_parts")
    receipt = {
        "archive": str(target),
        "archive_id": manifest["archive_id"],
        "cutoff": manifest["cutoff"],
        "rows_deleted": sum(r["deleted"] for r in results),
        "parts_deleted": sum(r["parts_deleted"] for r in results),
        "rollup_rows": sum(r["rollup_rows"] for r in results),
        "archive_bytes": manifest["archive_bytes"],
        "days": results,
        "reclaim": policy.RECLAIM,
        "finished_at": datetime.now(UTC).isoformat(),
    }
    archive.write_json(target / "apply-receipt.json", receipt)
    return receipt
