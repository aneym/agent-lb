from __future__ import annotations

import fcntl
from pathlib import Path
from typing import IO


def usage_refresh_lock_path() -> Path:
    return Path.home() / ".agent-lb" / "usage-refresh.lock"


def try_usage_refresh_process_lock(path: Path | None = None) -> IO[str] | None:
    lock_path = path or usage_refresh_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle
