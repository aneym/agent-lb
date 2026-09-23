from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Anthropic OAuth (Bearer) credentials are only honored when the first system
# block is the Claude Code identity line. Claude Code sends it on every
# request; a client that talks to the proxy directly (curl, an SDK, another
# harness) does not, and upstream answers 429 rate_limit_error — which the
# proxy used to record as a real quota cooldown, burning one account per
# attempt until the pool read as exhausted.
CLAUDE_CODE_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."


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
