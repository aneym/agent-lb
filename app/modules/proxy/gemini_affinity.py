from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from app.core.utils.client_session import get_thread_id


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:32]


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        parts = content.get("parts")
        if isinstance(parts, list):
            return "".join(
                part["text"] for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
    return ""


def _first_turn(body: dict[str, Any]) -> str | None:
    system = _text(body.get("systemInstruction", body.get("system_instruction")))
    contents = body.get("contents")
    turns = contents if isinstance(contents, list) else []
    first_user = next(
        (
            _text(item)
            for item in turns
            if isinstance(item, dict) and item.get("role", "user") == "user" and _text(item)
        ),
        "",
    )
    if not first_user:
        return None
    return json.dumps([system, first_user], ensure_ascii=False, separators=(",", ":"))


def gemini_sticky_key(model: str, body: dict[str, Any]) -> str | None:
    thread = get_thread_id()
    if thread:
        return f"gemini:{model}:thread:{_digest(thread)}"
    session = body.get("sessionId", body.get("session_id"))
    if isinstance(session, str) and session.strip():
        return f"gemini:{model}:session:{_digest(session.strip())}"
    material = _first_turn(body)
    return f"gemini:{model}:derived:{_digest(material)}" if material else None
