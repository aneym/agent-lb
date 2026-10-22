from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT_MAX_AGE_SECONDS = 600
SNAPSHOT_MAX_FUTURE_SECONDS = 60


def snapshot_advisory(snapshot_path: Path) -> str | None:
    try:
        snapshot = json.loads(snapshot_path.read_text())
    except FileNotFoundError:
        return "missing snapshot"
    except (OSError, json.JSONDecodeError):
        return "unparseable snapshot"
    try:
        if not isinstance(snapshot, dict):
            raise ValueError("snapshot is not an object")
        polled_at = str(snapshot["polled_at"])
        observed_at = datetime.fromisoformat(polled_at[:-1] + "+00:00" if polled_at.endswith("Z") else polled_at)
        if observed_at.tzinfo is None:
            raise ValueError("polled_at is not timezone-aware")
        age_seconds = (datetime.now(timezone.utc) - observed_at.astimezone(timezone.utc)).total_seconds()
        if age_seconds < -SNAPSHOT_MAX_FUTURE_SECONDS:
            return "snapshot dated in the future"
        if age_seconds > SNAPSHOT_MAX_AGE_SECONDS:
            return "stale snapshot"
        if snapshot["reachable"] is not True:
            return "snapshot reports unreachable"
        if snapshot["providers"]["anthropic"]["usable_count"] < 2:
            return "anthropic usable_count < 2"
    except Exception:
        return "internal error reading snapshot"
    return None


def first_snapshot_advisory(session_id: str, snapshot_path: Path) -> bool:
    if not session_id:
        return True
    try:
        try:
            mtime = snapshot_path.stat().st_mtime_ns
        except FileNotFoundError:
            mtime = None
        key = hashlib.sha256(json.dumps([session_id, str(snapshot_path), mtime]).encode()).hexdigest()
        state = Path(os.environ.get("SEAT_GUARD_STATE_DIR") or Path.home() / ".claude" / "state" / "seat-guard")
        state.mkdir(parents=True, exist_ok=True)
        with (state / key).open("x"):
            pass
    except FileExistsError:
        return False
    except OSError:
        return False
    return True
