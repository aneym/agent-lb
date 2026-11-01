from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ARCHIVE_ENV = "AGENT_LB_RETENTION_ARCHIVE_DIR"


def add_parser(subparsers) -> None:
    retention = subparsers.add_parser("retention", help="Archive and expire old request logs.")
    commands = retention.add_subparsers(dest="retention_command", required=True)
    run = commands.add_parser("run", help="Plan (default) or apply request_logs retention.")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Measure only; the default.")
    mode.add_argument("--apply", action="store_true", help="Archive, verify, fold into request_log_daily, delete.")
    run.add_argument("--days", type=int, default=None, help="Raw days to keep (default: the policy window).")
    run.add_argument("--archive-dir", type=Path, default=None, help=f"Archive volume (default: ${ARCHIVE_ENV}).")
    run.add_argument("--approval", type=Path, help="Destructive-data approval record; required with --apply.")
    run.add_argument("--receipt", type=Path, help="Write the JSON receipt here as well.")
    run.add_argument("--db", help="Database URL (default: AGENT_LB_DATABASE_URL, then the live service database).")
    restore = commands.add_parser("restore", help="Load an archive back and remove its rollup rows.")
    restore.add_argument("archive", type=Path, help="Archive directory holding manifest.json.")
    restore.add_argument("--db", help="Database URL (default: AGENT_LB_DATABASE_URL, then the live service database).")


def _connect(explicit: str | None):
    import psycopg

    from app.core.config.service_database import cli_database_url

    url = cli_database_url(explicit)
    if not url.startswith(("postgresql://", "postgresql+asyncpg://", "postgresql+psycopg://")):
        raise SystemExit("retention runs against PostgreSQL only")
    url = url.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg://", "postgresql://")
    return psycopg.connect(url, autocommit=True, cursor_factory=psycopg.ClientCursor)


def _lock(connection) -> None:
    from app.retention_policy import LOCK_KEY

    if not connection.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (LOCK_KEY,)).fetchone()[0]:
        raise SystemExit("another retention run holds the lock")


def _archive_dir(args) -> Path | None:
    value = args.archive_dir or os.environ.get(ARCHIVE_ENV)
    return Path(value).expanduser() if value else None


def _emit(payload: dict, receipt: Path | None) -> None:
    from app.retention_archive import write_json

    if receipt is not None:
        receipt.parent.mkdir(parents=True, exist_ok=True)
        write_json(receipt, payload)
    json.dump(payload, sys.stdout, indent=2, sort_keys=True, default=str)
    sys.stdout.write("\n")


def run(args) -> None:
    from app import retention_apply, retention_plan, retention_restore
    from app.retention_archive import ArchiveError, off_root
    from app.retention_policy import REQUEST_LOG_DAYS

    try:
        if args.retention_command == "restore":
            with _connect(args.db) as connection:
                _lock(connection)
                _emit(retention_restore.restore(connection, args.archive.expanduser().resolve()), None)
            return
        days = args.days or REQUEST_LOG_DAYS
        if days < 30:
            raise SystemExit("--days below 30 would cut into quota and monthly-limit windows")
        root = _archive_dir(args)
        if not args.apply:
            with _connect(args.db) as connection:
                _emit(retention_plan.plan(connection, days, root), args.receipt)
            return
        if root is None:
            raise SystemExit(f"--apply needs --archive-dir or ${ARCHIVE_ENV}")
        if args.approval is None or not args.approval.is_file() or args.approval.stat().st_size == 0:
            raise SystemExit("--apply needs --approval pointing at a destructive-data approval record")
        root = off_root(root)
        with _connect(args.db) as connection:
            _lock(connection)
            estimate = retention_plan.plan(connection, days, root)
            if not estimate["archive_dir"]["fits"]:
                raise SystemExit(f"{root} lacks room for the archive or would pass 70% used")
            _emit(retention_apply.apply(connection, root, days, args.approval.resolve()), args.receipt)
    except ArchiveError as exc:
        raise SystemExit(str(exc)) from exc
