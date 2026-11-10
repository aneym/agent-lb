from __future__ import annotations

import argparse
import json
import os
import resource
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import (
    usage_export_alert,
    usage_export_asked_by,
    usage_export_asked_by_records,
    usage_export_headroom,
    usage_export_hourly,
)
from app.core.config.service_database import LOCAL_DATABASE_URL, primary_database_url

USAGE_FILE = "usage-48h.json"
HEADROOM_FILE = "headroom.json"


def _plain_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg://", "postgresql://")


def _source(host: str, what: str) -> str:
    return f"agent-lb primary ({host}) Postgres agent_lb {what}; read-only snapshot by app.usage_export"


def collect(
    url: str, host: str, asked_by: Path | None, pulled: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    import psycopg

    with psycopg.connect(
        _plain_url(url),
        connect_timeout=15,
        options="-c default_transaction_read_only=on -c statement_timeout=120000",
    ) as connection:
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        connection.read_only = True
        with connection.cursor() as cursor:
            row = cursor.execute("SELECT now() AT TIME ZONE 'UTC'").fetchone()
            if row is None:
                raise RuntimeError("database returned no clock")
            now = row[0]
            generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            usage = usage_export_hourly.build(
                cursor, now, _source(host, "request_logs, deleted_at null, every status"), generated_at
            )
            turns, records = usage_export_asked_by_records.load(asked_by, now)
            usage["asked_by"] = usage_export_asked_by.build(cursor, now, turns, {**records, "pulled": pulled})
            headroom = usage_export_headroom.build(
                cursor, now, _source(host, "accounts, usage_history, additional_usage_history"), generated_at
            )
    return usage, headroom


def write_atomic(directory: Path, name: str, document: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    handle = tempfile.NamedTemporaryFile("w", dir=directory, prefix=f".{name}.", suffix=".tmp", delete=False)
    try:
        with handle:
            json.dump(document, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(handle.name, 0o644)
        os.replace(handle.name, target)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise
    return target


def push(paths: list[Path], destination: str) -> None:
    command = [
        "rsync",
        "--compress",
        "--times",
        "--mkpath",
        "--timeout=60",
        "-e",
        "ssh -o BatchMode=yes -o ConnectTimeout=15",
        *[str(path) for path in paths],
        destination.rstrip("/") + "/",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(f"rsync exited {result.returncode}: {result.stderr.strip()[:400]}")


def _cpu_seconds() -> float:
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return own.ru_utime + own.ru_stime + children.ru_utime + children.ru_stime


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Write agent-lb hourly usage and Anthropic headroom as JSON files.")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--push", help="rsync destination host:dir for the two files")
    parser.add_argument("--alert-state", type=Path, help="state file for the low pool headroom alert to factory")
    parser.add_argument("--asked-by", type=Path, help="directory of per-turn asker records (*.jsonl, read recursively)")
    parser.add_argument(
        "--asked-by-pull", action="append", default=[], help="host:dir of asker records to rsync into --asked-by/<host>"
    )
    args = parser.parse_args(argv)
    host = socket.gethostname().split(".")[0]
    url = os.environ.get("AGENT_LB_DATABASE_URL") or primary_database_url() or LOCAL_DATABASE_URL
    started = time.monotonic()
    try:
        pulled = usage_export_asked_by_records.pull(args.asked_by_pull, args.asked_by) if args.asked_by else []
        usage, headroom = collect(url, host, args.asked_by, pulled)
        paths = [write_atomic(args.out, USAGE_FILE, usage), write_atomic(args.out, HEADROOM_FILE, headroom)]
        if args.push:
            push(paths, args.push)
        alert = (
            usage_export_alert.check(headroom, args.alert_state, usage["generated_at"])
            if args.alert_state
            else "alert: off"
        )
    except Exception as exc:
        print(f"usage export: failed ({type(exc).__name__}: {str(exc)[:400]})", file=sys.stderr)
        raise SystemExit(1) from None
    sizes = ", ".join(f"{path.name} {path.stat().st_size} B" for path in paths)
    print(
        f"usage export: generated_at {usage['generated_at']} buckets {len(usage['buckets'])} "
        f"accounts {len(headroom['accounts_now'])} asked_by turns {usage['asked_by']['records']['turns']}; {sizes}; to {args.push or args.out}; "
        f"{alert}; wall {time.monotonic() - started:.2f} s cpu {_cpu_seconds():.2f} s",
        flush=True,
    )


if __name__ == "__main__":
    main()
