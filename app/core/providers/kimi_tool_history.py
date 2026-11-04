from __future__ import annotations

from hashlib import sha256
from typing import Any, TypeGuard


def normalize_tool_history(body: dict[str, Any]) -> dict[str, Any]:
    messages = body.get("messages")
    if not isinstance(messages, list):
        return body
    pending: set[str] = set()
    generated: list[str] = []
    normalized: list[Any] = []
    changed = False
    for message_index, message in enumerate(messages):
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            normalized.append(message)
            if isinstance(message, dict) and message.get("role") == "assistant":
                pending.clear()
                generated.clear()
            continue
        role = message.get("role")
        if role == "assistant":
            pending.clear()
            generated.clear()
        blocks: list[Any] = []
        for block_index, block in enumerate(content):
            if not isinstance(block, dict):
                blocks.append(block)
                continue
            kind = block.get("type")
            if kind == "tool_use" and role == "assistant":
                call_id = block.get("id")
                if not _valid_id(call_id):
                    digest = sha256(f"{message_index}:{block_index}".encode()).hexdigest()[:24]
                    call_id = f"tool_lb_{digest}"
                    generated.append(call_id)
                    block = {**block, "id": call_id}
                    changed = True
                pending.add(call_id)
            elif kind == "tool_result":
                call_id = block.get("tool_use_id")
                if role == "user" and not _valid_id(call_id) and generated:
                    call_id = generated.pop(0)
                    block = {**block, "tool_use_id": call_id}
                    changed = True
                if role == "user" and _valid_id(call_id) and call_id in pending:
                    pending.remove(call_id)
                else:
                    blocks.extend(_orphan_content(block))
                    changed = True
                    continue
            blocks.append(block)
        normalized.append({**message, "content": blocks} if blocks != content else message)
    return {**body, "messages": normalized} if changed else body


def _valid_id(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value.strip())


def _orphan_content(block: dict[str, Any]) -> list[dict[str, Any]]:
    content = block.get("content")
    prefix = "Previous tool result (call unavailable):"
    if isinstance(content, str):
        return [{"type": "text", "text": f"{prefix}\n{content}"}]
    blocks = [{"type": "text", "text": prefix}]
    if isinstance(content, list):
        blocks.extend(part for part in content if isinstance(part, dict) and part.get("type") in {"text", "image"})
    return blocks
