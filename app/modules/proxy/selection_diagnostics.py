from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.core.balancer.failures import SELECTOR_RETRY_HINT_MAX_SECONDS
from app.core.errors import OpenAIErrorEnvelope

if TYPE_CHECKING:
    from app.modules.proxy.load_balancer import AccountSelection


def selection_error_extras(
    selection: "AccountSelection",
    *,
    requested_model: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:

    extras: dict[str, Any] = {}
    current = time.time() if now is None else now

    retry_at = getattr(selection, "retry_at", None)
    if isinstance(retry_at, datetime):
        resets_at = retry_at.timestamp()
        extras["resets_at"] = int(resets_at)
        extras["resets_in_seconds"] = max(0, math.ceil(resets_at - current))

    excluded = list(getattr(selection, "excluded_accounts", None) or [])
    if excluded:
        accounts = [
            {
                "id": entry.account_id,
                "status": entry.status,
                "reset_at": entry.reset_at.isoformat() if entry.reset_at else None,
                "reason": entry.deactivation_reason,
            }
            for entry in excluded
        ]
        diagnostics: dict[str, Any] = {"degraded": True, "accounts": accounts}
        if requested_model:
            diagnostics["requested_model"] = requested_model
        if isinstance(retry_at, datetime):
            diagnostics["earliest_recovery_at"] = retry_at.astimezone(timezone.utc).isoformat()
        extras["diagnostics"] = diagnostics
    return extras


def enrich_selection_error(
    envelope: OpenAIErrorEnvelope,
    selection: "AccountSelection",
    *,
    requested_model: str | None = None,
) -> OpenAIErrorEnvelope:

    extras = selection_error_extras(selection, requested_model=requested_model)
    if extras:
        envelope["error"].update(extras)  # type: ignore[typeddict-item]
    return envelope


def retry_after_seconds_from_error_detail(detail: Any) -> int | None:

    if isinstance(detail, dict):
        value = detail.get("resets_in_seconds")
    else:
        value = getattr(detail, "resets_in_seconds", None)
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    return min(math.ceil(seconds), SELECTOR_RETRY_HINT_MAX_SECONDS)
