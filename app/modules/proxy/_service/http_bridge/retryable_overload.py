from __future__ import annotations

import json

OVERLOADED_CODE = "server_is_overloaded"
RETRYABLE_CODE = "server_error"


def retryable_overload_event(block: str) -> str:
    if OVERLOADED_CODE not in block:
        return block
    prefix, marker, data = block.partition("data: ")
    if not marker:
        prefix, data = "", block
    body = data.rstrip("\n")
    try:
        payload = json.loads(body)
    except ValueError:
        return block
    if not isinstance(payload, dict):
        return block
    holder = payload.get("response") if payload.get("type") == "response.failed" else payload
    error = holder.get("error") if isinstance(holder, dict) else None
    if not isinstance(error, dict) or error.get("code") != OVERLOADED_CODE:
        return block
    error["code"] = RETRYABLE_CODE
    rewritten = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return f"{prefix}{marker}{rewritten}{data[len(body) :]}"
