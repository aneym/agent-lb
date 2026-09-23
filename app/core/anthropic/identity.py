from __future__ import annotations

import re
import time
from collections import OrderedDict
from collections.abc import Mapping
from threading import Lock
from typing import Any

# Anthropic OAuth (Bearer) credentials are only honored when the first system
# block is the Claude Code identity line. Claude Code sends it on every
# request; a client that talks to the proxy directly (curl, an SDK, another
# harness) does not, and upstream answers 429 rate_limit_error — which the
# proxy used to record as a real quota cooldown, burning one account per
# attempt until the pool read as exhausted.
CLAUDE_CODE_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."
_BILLING_SESSION_TTL_SECONDS = 24 * 60 * 60
_BILLING_SESSION_MAX_ENTRIES = 4096
_billing_session_values: OrderedDict[str, tuple[float, str, str]] = OrderedDict()
_billing_session_lock = Lock()
_CCH_VALUE = re.compile(r"\bcch=([^;]*)")
_PROMPT_ID_VALUE = re.compile(r"\bcc_prompt_id=([^;]*)")


def _identity_block() -> dict[str, Any]:
    return {"type": "text", "text": CLAUDE_CODE_IDENTITY}


def _carries_identity(system: Any) -> bool:
    """True when the payload already leads with the Claude Code identity."""
    if isinstance(system, str):
        return system.startswith(CLAUDE_CODE_IDENTITY)
    if isinstance(system, list):
        for block in system:
            if isinstance(block, Mapping):
                text = block.get("text")
                return isinstance(text, str) and text.startswith(CLAUDE_CODE_IDENTITY)
            return False
    return False


def ensure_claude_code_identity(system: Any) -> list[dict[str, Any]] | Any:
    """Return a ``system`` value whose first block is the Claude Code identity.

    The caller's own system prompt is preserved as the block after it, so
    behavior the client asked for is unchanged. A payload that already leads
    with the identity (every real Claude Code request) is returned untouched,
    which keeps upstream prompt caching intact.
    """
    if _carries_identity(system):
        return system
    if system is None:
        return [_identity_block()]
    if isinstance(system, str):
        if not system.strip():
            return [_identity_block()]
        return [_identity_block(), {"type": "text", "text": system}]
    if isinstance(system, list):
        return [_identity_block(), *system]
    # Unknown shape: leave it alone rather than mangle a payload upstream may
    # understand better than we do.
    return system


def ensure_claude_code_identity_body(body: Mapping[str, Any]) -> dict[str, Any]:
    """Apply :func:`ensure_claude_code_identity` to a Messages API body."""
    system = body.get("system")
    ensured = ensure_claude_code_identity(system)
    if ensured is system:
        return dict(body)
    return {**body, "system": ensured}


def move_volatile_billing_block_after_cache_prefix(body: Mapping[str, Any]) -> dict[str, Any]:
    """Keep Claude Code's per-turn billing marker outside cached system prefixes."""
    system = body.get("system")
    if not isinstance(system, list):
        return dict(body)
    billing_index = next(
        (
            index
            for index, block in enumerate(system)
            if isinstance(block, Mapping)
            and isinstance(block.get("text"), str)
            and block["text"].startswith("x-anthropic-billing-header: ")
            and "cc_prompt_id=" in block["text"]
        ),
        None,
    )
    if billing_index is None:
        return dict(body)
    cache_indexes = [
        index
        for index, block in enumerate(system)
        if index > billing_index and isinstance(block, Mapping) and block.get("cache_control")
    ]
    if not cache_indexes:
        return dict(body)
    last_cache_index = cache_indexes[-1]
    reordered = [
        *system[:billing_index],
        *system[billing_index + 1 : last_cache_index + 1],
        system[billing_index],
        *system[last_cache_index + 1 :],
    ]
    return {**body, "system": reordered}


def stabilize_billing_marker_for_session(body: Mapping[str, Any], session_id: str | None) -> dict[str, Any]:
    """Pin only the billing marker's volatile values for a Claude session."""
    if not session_id:
        return dict(body)
    system = body.get("system")
    if not isinstance(system, list):
        return dict(body)
    for index, block in enumerate(system):
        if not isinstance(block, Mapping):
            continue
        marker = block.get("text")
        if not isinstance(marker, str) or not marker.startswith("x-anthropic-billing-header: "):
            continue
        cch = _CCH_VALUE.search(marker)
        prompt_id = _PROMPT_ID_VALUE.search(marker)
        if cch is None or prompt_id is None:
            break
        now = time.monotonic()
        with _billing_session_lock:
            while _billing_session_values:
                oldest = next(iter(_billing_session_values))
                if _billing_session_values[oldest][0] > now:
                    break
                _billing_session_values.popitem(last=False)
            stored = _billing_session_values.get(session_id)
            if stored is None:
                values = (cch.group(1), prompt_id.group(1))
            else:
                values = stored[1:]
            _billing_session_values[session_id] = (now + _BILLING_SESSION_TTL_SECONDS, *values)
            _billing_session_values.move_to_end(session_id)
            if len(_billing_session_values) > _BILLING_SESSION_MAX_ENTRIES:
                _billing_session_values.popitem(last=False)
        stable_marker = _CCH_VALUE.sub(lambda match: match.group(0)[:4] + values[0], marker, count=1)
        stable_marker = _PROMPT_ID_VALUE.sub(
            lambda match: match.group(0)[:13] + values[1], stable_marker, count=1
        )
        if stable_marker == marker:
            return dict(body)
        stable_block = {**block, "text": stable_marker}
        return {**body, "system": [*system[:index], stable_block, *system[index + 1 :]]}
    return dict(body)
