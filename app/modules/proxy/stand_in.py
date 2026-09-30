from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.modules.routing_policy.service import _live_paths

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()
_DEFAULT_MODELS = {"orchestrator": "sol-latest-high", "lane-tab": "sol-latest-high"}


def is_review_lane(lane: str | None) -> bool:
    return bool(lane and re.search(r"(?<![A-Za-z])(?:review|verif|audit)", lane, re.IGNORECASE))


def stand_in_model(intent: str | None) -> str | None:
    if not intent:
        return None
    try:
        table = json.loads(_live_paths()[0].read_text())
        models = table.get("policy", {}).get("stand_in", _DEFAULT_MODELS)
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(models, dict):
        return None
    model = models.get(intent)
    return model if isinstance(model, str) and model else None


def _path() -> Path:
    return Path(os.environ.get("AGENT_LB_STAND_IN_FILE") or "~/.agent-lb/stand-ins.json").expanduser()


def _read(path: Path) -> dict[str, list[dict[str, Any]]]:
    try:
        state = json.loads(path.read_text())
        if not isinstance(state, dict) or any(
            not isinstance(state.get(key), list)
            or any(not isinstance(row, dict) or not isinstance(row.get("session_id"), str) for row in state[key])
            for key in ("active", "recent")
        ):
            raise ValueError("invalid stand-in store shape")
        return state
    except FileNotFoundError:
        return {"active": [], "recent": []}
    except (OSError, ValueError):
        logger.warning("Unable to read stand-in store; using an empty store")
        return {"active": [], "recent": []}


def _write(path: Path, state: dict[str, list[dict[str, Any]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(state))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _timestamp(value: int | None = None) -> str:
    now = datetime.now(timezone.utc) if value is None else datetime.fromtimestamp(value, timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def record_failure(
    session_id: str,
    *,
    lane: str | None,
    intent: str,
    intended: str,
    running: str,
    effort: str | None,
    reason: str,
    retry_at: int | None,
) -> None:
    with _LOCK:
        path = _path()
        state = _read(path)
        now = _timestamp()
        record = next((row for row in state["active"] if row["session_id"] == session_id), None)
        if record is None:
            record = {
                "session_id": session_id,
                "lane": lane,
                "intent": intent,
                "intended": intended,
                "running": running,
                "effort": effort,
                "since": now,
                "requests": 0,
            }
            state["active"].append(record)
        record.update(
            reason=reason,
            expected_return=_timestamp(retry_at) if retry_at is not None else None,
            last_at=now,
        )
        record["requests"] += 1
        _write(path, state)


def record_success(session_id: str) -> bool:
    with _LOCK:
        path = _path()
        state = _read(path)
        for index, record in enumerate(state["active"]):
            if record["session_id"] == session_id:
                state["active"].pop(index)
                record["returned_at"] = _timestamp()
                state["recent"].insert(0, record)
                state["recent"] = state["recent"][:50]
                _write(path, state)
                return True
        return False


def snapshot() -> dict[str, list[dict[str, Any]]]:
    with _LOCK:
        return _read(_path())
