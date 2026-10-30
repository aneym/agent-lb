from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

TOOL_CHANGE_BLOCK_TYPES = frozenset({"tool_addition", "tool_removal"})
TOOL_CHANGE_BETA_PREFIXES = ("mid-conversation-tool-changes-", "inline-tools-")


def flatten_tool_changes(body: dict[str, Any]) -> dict[str, Any]:
    messages = body.get("messages")
    if not isinstance(messages, list):
        return body
    tools = {tool["name"]: tool for tool in body.get("tools") or [] if isinstance(tool, dict) and "name" in tool}
    kept_messages: list[Any] = []
    changed = False
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list) or not any(_is_tool_change(block) for block in content):
            kept_messages.append(message)
            continue
        changed = True
        kept: list[Any] = []
        for block in content:
            if not _is_tool_change(block):
                kept.append(block)
            elif block["type"] == "tool_addition":
                definition = (block.get("tool") or {}).get("definition")
                if isinstance(definition, dict) and "name" in definition:
                    tools[definition["name"]] = definition
            else:
                tools.pop(str((block.get("tool") or {}).get("name") or block.get("name")), None)
        if kept:
            kept_messages.append({**message, "content": kept})
    if not changed:
        return body
    return {**body, "messages": kept_messages, "tools": list(tools.values())}


def drop_tool_change_betas(headers: MutableMapping[str, str]) -> None:
    key = next((k for k in headers if k.lower() == "anthropic-beta"), None)
    if key is None:
        return
    kept = [
        beta.strip()
        for beta in headers[key].split(",")
        if beta.strip() and not beta.strip().lower().startswith(TOOL_CHANGE_BETA_PREFIXES)
    ]
    if kept:
        headers[key] = ",".join(kept)
    else:
        del headers[key]


def _is_tool_change(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") in TOOL_CHANGE_BLOCK_TYPES
