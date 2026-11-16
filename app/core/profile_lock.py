from __future__ import annotations

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager

from app.core.browser_profiles import registry_path


@contextmanager
def profile_held(email: str) -> Iterator[bool]:
    path = registry_path().parent / "profile-locks" / f"{email.strip().lower()}.lock"
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
