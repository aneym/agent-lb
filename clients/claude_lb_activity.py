from __future__ import annotations  # noqa: I001


import os
import re
import threading
import time
import uuid
from pathlib import Path


class LbActivity:
    def __init__(self, path: Path, *, only_if_exists: bool = False) -> None:
        self.path = path
        self.only_if_exists = only_if_exists
        self.lock = threading.Lock()
        self.inflight = 0
        self.last_progress = 0.0
        self.last_write = 0.0

    def _write(self) -> None:
        if self.only_if_exists:
            try:
                with self.path.open("r+") as stream:
                    stream.seek(0)
                    stream.write(f"{self.inflight} {self.last_progress}\n")
                    stream.truncate()
                self.last_write = self.last_progress
            except OSError:
                pass
            return
        tmp = self.path.with_name(f"{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(f"{self.inflight} {self.last_progress}\n")
            os.replace(tmp, self.path)
            self.last_write = self.last_progress
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass

    def start(self) -> None:
        with self.lock:
            self.inflight += 1
            self.last_progress = time.time()
            self._write()

    def progress(self) -> None:
        with self.lock:
            self.last_progress = time.time()
            if self.last_progress - self.last_write >= 2.0:
                self._write()

    def end(self) -> None:
        with self.lock:
            self.inflight -= 1
            self.last_progress = time.time()
            self._write()


def proxy_ready_path(session_id: str) -> Path:
    shim_dir = Path.home() / ".agent-lb" / "shims"
    shim_dir.mkdir(parents=True, exist_ok=True)
    try:
        shim_dir.chmod(0o700)
    except OSError:
        pass
    return shim_dir / f"{session_id}.proxy"


def activity_path(session_id: str, parent_pid: int) -> Path:
    return proxy_ready_path(session_id).parent / f"{session_id}.{parent_pid}.activity"


def client_activity_path(client_session_id: str) -> Path | None:
    if re.fullmatch(r"[A-Za-z0-9-]{1,128}", client_session_id) is None:
        return None
    return proxy_ready_path("x").parent / f"client-{client_session_id}.activity"
