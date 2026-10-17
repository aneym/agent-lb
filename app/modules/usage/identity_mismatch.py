from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

ESCALATION_THRESHOLD = 3

_lock = threading.Lock()
_state: dict[str, dict[str, Any]] = {}


def record_identity_mismatch(
    account_id: str,
    *,
    stored_plan_type: str | None,
    payload_plan_type: str | None,
    stored_workspace_id: str | None = None,
    payload_workspace_id: str | None = None,
) -> dict[str, Any]:

    now = datetime.now(timezone.utc)
    with _lock:
        entry = _state.get(account_id)
        if entry is None:
            entry = {
                "count": 0,
                "first_at": now,
                "last_at": now,
                "stored_plan_type": stored_plan_type,
                "payload_plan_type": payload_plan_type,
                "stored_workspace_id": stored_workspace_id,
                "payload_workspace_id": payload_workspace_id,
            }
            _state[account_id] = entry
        entry["count"] += 1
        entry["last_at"] = now
        entry["stored_plan_type"] = stored_plan_type
        entry["payload_plan_type"] = payload_plan_type
        entry["stored_workspace_id"] = stored_workspace_id
        entry["payload_workspace_id"] = payload_workspace_id
        count = entry["count"]
        snapshot = dict(entry)
    if count == ESCALATION_THRESHOLD:
        logger.error(
            "Usage refresh identity mismatch persists (%s consecutive cycles); "
            "account is running blind on stale identity until resolved "
            "account_id=%s stored_plan_type=%s payload_plan_type=%s "
            "stored_workspace_id=%s payload_workspace_id=%s",
            count,
            account_id,
            stored_plan_type,
            payload_plan_type,
            stored_workspace_id,
            payload_workspace_id,
        )
    return snapshot


def clear_identity_mismatch(account_id: str) -> None:

    with _lock:
        _state.pop(account_id, None)


def get_identity_mismatch(account_id: str) -> dict[str, Any] | None:

    with _lock:
        entry = _state.get(account_id)
        if entry is None or entry["count"] < ESCALATION_THRESHOLD:
            return None
        return dict(entry)
