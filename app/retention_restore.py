from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app import retention_archive as archive
from app import retention_policy as policy


def _load(cursor, table: str, columns: list[str], root: Path, entry: dict) -> None:
    staging = f"restore_{table}"
    cursor.execute(
        f"CREATE TEMP TABLE IF NOT EXISTS {staging} AS SELECT {policy.quoted(columns)} FROM {table} WITH NO DATA"
    )
    cursor.execute(f"TRUNCATE {staging}")
    with cursor.copy(f"COPY {staging} ({policy.quoted(columns)}) FROM STDIN WITH (FORMAT csv, HEADER true)") as copy:
        for block in archive.read_raw(root / entry["file"]):
            copy.write(block)
    cursor.execute(f"INSERT INTO {table} ({policy.quoted(columns)}) SELECT {policy.quoted(columns)} FROM {staging}")


def restore(connection, root: Path) -> dict:
    manifest = archive.read_manifest(root)
    columns = manifest["columns"]
    applied = [entry for entry in manifest["days"] if entry["applied_at"]]
    for entry in applied:
        for item in entry["files"].values():
            archive.verify_file(root, item)
    results = []
    with connection.transaction():
        cursor = connection.cursor()
        cursor.execute("SET LOCAL lock_timeout = '5s'")
        cursor.execute("SELECT count(*) FROM request_log_daily WHERE archive_id = %s", (manifest["archive_id"],))
        rollup_rows = cursor.fetchone()[0]
        for entry in applied:
            day = policy.day_of(datetime.fromisoformat(entry["day"]).date())
            params = {"start": day.start, "end": day.end, "max_id": manifest["max_id"]}
            _load(cursor, "request_logs", columns["request_logs"], root, entry["files"]["request_logs"])
            _load(
                cursor,
                "request_context_parts",
                columns["request_context_parts"],
                root,
                entry["files"]["request_context_parts"],
            )
            cursor.execute(policy.log_checksum(columns["request_logs"]), params)
            logs = tuple(cursor.fetchone())
            cursor.execute(policy.part_checksum(columns["request_context_parts"]), params)
            parts = tuple(cursor.fetchone())
            if logs != (entry["rows"], entry["md5"]) or parts != (entry["parts_rows"], entry["parts_md5"]):
                raise archive.ArchiveError(f"{entry['day']} restored rows do not match the archive; rolled back")
            exported = {
                "request_logs": archive.raw_export_sha256(cursor, policy.log_select(columns["request_logs"]), params),
                "request_context_parts": archive.raw_export_sha256(
                    cursor, policy.part_select(columns["request_context_parts"]), params
                ),
            }
            for table, digest in exported.items():
                if digest != entry["files"][table]["raw_sha256"]:
                    raise archive.ArchiveError(
                        f"{entry['day']} {table} re-export differs from the archive; rolled back"
                    )
            results.append({"day": entry["day"], "rows": entry["rows"], "parts_rows": entry["parts_rows"]})
        cursor.execute("DELETE FROM request_log_daily WHERE archive_id = %s", (manifest["archive_id"],))
        if cursor.rowcount != rollup_rows:
            raise archive.ArchiveError("rollup rows changed during restore; rolled back")
    for entry in applied:
        entry["restored_at"] = datetime.now(UTC).isoformat()
        entry["applied_at"] = None
    archive.write_json(root / "manifest.json", manifest)
    receipt = {
        "archive_id": manifest["archive_id"],
        "restored_rows": sum(r["rows"] for r in results),
        "restored_parts": sum(r["parts_rows"] for r in results),
        "rollup_rows_removed": rollup_rows,
        "days": results,
        "finished_at": datetime.now(UTC).isoformat(),
    }
    archive.write_json(root / "restore-receipt.json", receipt)
    return receipt
