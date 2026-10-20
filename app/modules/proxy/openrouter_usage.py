from __future__ import annotations

import json

from app.core.anthropic.models import AnthropicErrorEvent, AnthropicUsage


def chat_usage(payload: dict) -> AnthropicUsage | None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    return AnthropicUsage(
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
        cache_read_input_tokens=(usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
        cost=usage.get("cost"),
    )


def collect_chat_usage(buffer: str, chunk: bytes, usage: AnthropicUsage | None):
    buffer += chunk.decode("utf-8", errors="replace")
    buffer = buffer.replace("\r\n", "\n")
    saw_output = False
    error = None
    while "\n\n" in buffer:
        block, buffer = buffer.split("\n\n", 1)
        for line in block.splitlines():
            if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                continue
            payload = json.loads(line[5:])
            if "error" in payload:
                error = AnthropicErrorEvent(type="error", error=payload["error"])
            usage = chat_usage(payload) or usage
            saw_output |= bool(payload.get("choices"))
    return buffer, usage, error, saw_output
