from __future__ import annotations

import json
from hashlib import sha256
from typing import Any, TypeGuard


def normalize_tool_history(body: dict[str, Any]) -> dict[str, Any]:
    messages = body.get("messages")
    if not isinstance(messages, list):
        return body
    pending: set[str] = set()
    generated: dict[str, list[str]] = {}
    normalized: list[Any] = []
    for message_index, message in enumerate(messages):
        if not isinstance(message, dict):
            normalized.append(message)
            continue
        role = message.get("role")
        if role == "assistant":
            _complete_pending(normalized, pending)
            generated.clear()
        content = message.get("content")
        if not isinstance(content, list):
            normalized.append(message)
            continue
        results: list[Any] = []
        blocks: list[Any] = []
        reserved = {
            block["id"]
            for block in content
            if isinstance(block, dict) and block.get("type") == "tool_use" and _valid_id(block.get("id"))
        }
        for block_index, block in enumerate(content):
            if not isinstance(block, dict):
                blocks.append(block)
                continue
            kind = block.get("type")
            if kind == "tool_use" and role == "assistant":
                call_id = block.get("id")
                if not _valid_id(call_id) or call_id in pending:
                    original = call_id if _valid_id(call_id) else ""
                    digest = sha256(f"{message_index}:{block_index}".encode()).hexdigest()[:24]
                    call_id = f"tool_lb_{digest}"
                    while call_id in reserved:
                        call_id += "_"
                    generated.setdefault(original, []).append(call_id)
                    block = {**block, "id": call_id}
                pending.add(call_id)
            elif kind == "tool_result":
                call_id = block.get("tool_use_id")
                original = call_id if _valid_id(call_id) else ""
                replacements = generated.get(original, [])
                if role == "user" and original not in pending and replacements:
                    call_id = replacements.pop(0)
                    block = {**block, "tool_use_id": call_id}
                if role == "user" and _valid_id(call_id) and call_id in pending:
                    pending.remove(call_id)
                    if not block.get("content"):
                        block = {**block, "content": "(empty tool result)"}
                    results.append(block)
                else:
                    blocks.extend(_orphan_content(block))
                continue
            blocks.append(block)
        normalized.append({**message, "content": [*results, *blocks]})
    if normalized and isinstance(normalized[-1], dict) and normalized[-1].get("role") != "assistant":
        _complete_pending(normalized, pending)
    return {**body, "messages": normalized} if normalized != messages else body


def _complete_pending(messages: list[Any], pending: set[str]) -> None:
    if pending:
        results = [
            {"type": "tool_result", "tool_use_id": call_id, "content": "(tool result unavailable)", "is_error": True}
            for call_id in sorted(pending)
        ]
        if messages and isinstance(messages[-1], dict) and messages[-1].get("role") == "user":
            message = messages[-1]
            content = message.get("content")
            blocks = content if isinstance(content, list) else [{"type": "text", "text": content or "Continue."}]
            messages[-1] = {**message, "content": [*results, *blocks]}
        else:
            messages.append({"role": "user", "content": results})
        pending.clear()


def _valid_id(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value.strip())


def _orphan_content(block: dict[str, Any]) -> list[dict[str, Any]]:
    content = block.get("content")
    prefix = "Previous tool result (call unavailable):"
    if block.get("is_error"):
        prefix += " (error)"
    if isinstance(content, str):
        return [{"type": "text", "text": f"{prefix}\n{content or '(empty tool result)'}"}]
    blocks = [{"type": "text", "text": prefix}]
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") in {"text", "image"}:
                blocks.append(part)
            else:
                blocks.append({"type": "text", "text": json.dumps(part, ensure_ascii=False)})
    return blocks
