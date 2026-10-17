from __future__ import annotations

from collections.abc import Mapping
from typing import Any

CLAUDE_CODE_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."


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

    text = _first_text(system)
    return text is not None and (text.startswith(CLAUDE_CODE_IDENTITY) or text.startswith(CLAUDE_CODE_BILLING_PREFIX))


def ensure_claude_code_identity(system: Any) -> list[dict[str, Any]] | Any:

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

    return system


def ensure_claude_code_identity_body(body: Mapping[str, Any]) -> dict[str, Any]:

    system = body.get("system")
    ensured = ensure_claude_code_identity(system)
    if ensured is system:
        return dict(body)
    return {**body, "system": ensured}
