from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

DEFAULT_DB = "postgresql://agent_lb:agent_lb@127.0.0.1:5432/agent_lb"

PSQL = shutil.which("psql") or next(
    (p for p in ("/opt/homebrew/bin/psql", "/usr/local/bin/psql") if os.access(p, os.X_OK)), "psql")


def utc(value: str) -> datetime:
    value = value.strip()
    duration = re.fullmatch(r"(\d+)([hd])", value)
    if duration:
        return datetime.now(timezone.utc) - timedelta(**{{"h": "hours", "d": "days"}[duration[2]]: int(duration[1])})
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def sql_time(value: datetime) -> str:
    return "'" + value.replace(tzinfo=None).isoformat(sep=" ") + "'"


def query(sql: str) -> list[list[str]]:
    dsn = os.environ.get("AGENT_LB_DATABASE_URL") or DEFAULT_DB
    dsn = re.sub(r"^postgresql\+\w+://", "postgresql://", dsn)
    result = subprocess.run(
        [PSQL, "-X", "-At", "-F|", "-v", "ON_ERROR_STOP=1", "-c", sql, dsn],
        text=True, capture_output=True, timeout=90,
    )
    if result.returncode:

        errors = [line for line in result.stderr.splitlines() if line.startswith("ERROR:")]
        raise RuntimeError("database query failed (psql exit %s): %s" %
                           (result.returncode, errors[0] if errors else "connection or query error"))
    return [line.split("|") for line in result.stdout.splitlines() if line]
