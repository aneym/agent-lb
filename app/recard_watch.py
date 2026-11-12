from __future__ import annotations

import fcntl
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.core.recards import registry_path

ROOT = Path(__file__).resolve().parent.parent


def _lock_path(email: str, kind: str) -> Path:
    return registry_path().parent / f"recard-{email}.{kind}.lock"


@contextmanager
def held(email: str, kind: str) -> Iterator[bool]:
    path = _lock_path(email, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def watching(email: str) -> bool:
    with held(email, "watch") as free:
        return not free


def spawn(email: str) -> bool:
    if watching(email):
        return False
    log = registry_path().parent / "recard-watch.log"
    with open(log, "a") as stream:
        subprocess.Popen(
            [sys.executable, "-m", "app.cli", "recard", "watch", email],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=stream,
            start_new_session=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    return True
