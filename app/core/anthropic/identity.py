from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Anthropic OAuth (Bearer) credentials are only honored for Claude Code
# payloads. A client that talks to the proxy directly (curl, an SDK, another
# harness) sends no Claude Code marker, and upstream answers 429
# rate_limit_error — which the proxy used to record as a real quota cooldown,
# burning one account per attempt until the pool read as exhausted.
CLAUDE_CODE_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."
# Claude Code 2.1.280+ sends a per-request billing block as the FIRST system
# block (its cch value changes on every request). Anthropic recognizes it only
# in that position and keeps it out of the prompt-cache prefix. Anything that
# pushes it to index 1 (e.g. prepending an identity block) turns it into
# ordinary prompt text, and every request then rewrites its whole context.
CLAUDE_CODE_BILLING_PREFIX = "x-anthropic-billing-header:"


def _identity_block() -> dict[str, Any]:
    return {"type": "text", "text": CLAUDE_CODE_IDENTITY}


def _first_text(system: Any) -> str | None:
    if isinstance(system, str):
        return system
    if isinstance(system, list) and system:
        block = system[0]
        if isinstance(block, Mapping):
            text = block.get("text")
            return text if isinstance(text, str) else None
    return None


def _is_claude_code_payload(system: Any) -> bool:
    """True when the payload already carries a Claude Code marker in first position."""
    text = _first_text(system)
    return text is not None and (text.startswith(CLAUDE_CODE_IDENTITY) or text.startswith(CLAUDE_CODE_BILLING_PREFIX))


def ensure_claude_code_identity(system: Any) -> list[dict[str, Any]] | Any:
    """Return a ``system`` value that Anthropic's OAuth endpoint will serve.

    A real Claude Code payload (leading identity line, or leading billing
    block) is returned untouched so upstream prompt caching works exactly as it
    does for a direct Claude Code session. Other payloads get the identity
    prepended; the caller's own system prompt is preserved after it.
    """
    if _is_claude_code_payload(system):
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
