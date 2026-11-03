from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def startup_failure() -> float | None:
    database = Path.home() / ".t3/userdata/statev2.sqlite"
    if not database.is_file():
        return None
    cutoff = (
        datetime.fromtimestamp(time.time() - 900, timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    deadline = time.monotonic() + 4
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1) as connection:
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        for at, kind, payload in connection.execute(
            "SELECT occurred_at, event_type, payload_json FROM orchestration_events ORDER BY sequence DESC"
        ):
            if at < cutoff:
                break
            if kind not in (
                "turn-item.updated",
                "run.updated",
                "node.updated",
                "provider-session.updated",
                "provider-thread.updated",
                "run-attempt.updated",
            ):
                continue
            document = json.loads(payload)
            failure = document.get("failure") or {}
            message = failure.get("message", "") if isinstance(failure, dict) else ""
            if "Codex app-server did not answer thread/start within" in message:
                return datetime.fromisoformat(at.replace("Z", "+00:00")).timestamp()
    return None


def sample() -> dict:
    if sys.platform == "linux":
        memory = Path("/proc/meminfo").read_text()
        match = re.search(r"MemAvailable:\s+(\d+)", memory)
        if match is None:
            raise ValueError("missing MemAvailable")
        available = float(match[1]) / 1024
        pressure = Path("/proc/pressure/memory")
        match = re.search(r"^full .*?avg60=([\d.]+)", pressure.read_text(), re.M) if pressure.exists() else None
        full = float(match[1]) if match else None
    else:
        stats = subprocess.run(["vm_stat"], capture_output=True, text=True, check=True, timeout=2).stdout
        page = int(re.search(r"page size of (\d+)", stats)[1])
        pages = sum(int(re.search(rf"Pages {name}:\s+(\d+)", stats)[1]) for name in ("free", "inactive", "speculative"))
        available, full = pages * page / 1048576, 0.0
    processes = subprocess.run(
        ["ps", "-axo", "comm=,args="], capture_output=True, text=True, check=True, timeout=2
    ).stdout
    count = sum(bool(re.search(r"^\S*codex\s+.*\bapp-server\b", line)) for line in processes.splitlines())
    return {
        "at": time.time(),
        "available_mb": available,
        "full_avg60": full,
        "codex_servers": count,
        "timeout_at": startup_failure(),
    }


if __name__ == "__main__":
    try:
        print(json.dumps(sample()))
    except (OSError, ValueError, TypeError, sqlite3.Error, subprocess.SubprocessError):
        print(json.dumps({"error": "host readiness probe unavailable"}))
