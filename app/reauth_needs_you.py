from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from pathlib import Path

from app.core.state_dir import state_dir
from app.reauth_ask import closure, withdraw_ask


def _path() -> Path:
    return state_dir() / "reauth-verified"


def enqueue_verified(account_id: str) -> None:
    directory = _path()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    receipt = directory / hashlib.sha256(account_id.encode()).hexdigest()
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=".new-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(account_id)
        os.replace(temporary, receipt)
    finally:
        Path(temporary).unlink(missing_ok=True)


def reconcile_verified() -> None:
    root = state_dir()
    directory = _path()
    if not directory.is_dir():
        return
    with (root / "auto-reauth.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        state = root / "auto-reauth-state.json"
        try:
            entries = json.loads(state.read_text())
        except FileNotFoundError:
            entries = {}
        if not isinstance(entries, dict):
            raise ValueError("Invalid reauth job state")
        consumed = []
        for receipt in directory.iterdir():
            if receipt.name.startswith(".") or not receipt.is_file():
                continue
            account_id = receipt.read_text()
            if receipt.name != hashlib.sha256(account_id.encode()).hexdigest():
                continue
            entry = entries.get(account_id)
            if isinstance(entry, dict) and entry.get("askId"):
                if not closure(entry):
                    withdraw_ask(entry, dry_run=False)
                if entry.get("askId"):
                    continue
            consumed.append(receipt)
        if not consumed:
            return
        if state.exists():
            fd, temporary = tempfile.mkstemp(dir=root, prefix=".auto-reauth-state-")
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump(entries, stream, indent=1, sort_keys=True)
                    stream.write("\n")
                os.replace(temporary, state)
            finally:
                Path(temporary).unlink(missing_ok=True)
        for receipt in consumed:
            receipt.unlink(missing_ok=True)


if __name__ == "__main__":
    reconcile_verified()
