from __future__ import annotations

import json
from dataclasses import dataclass

IMAGE_CHARS = 6400
MAX_BODY_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class PartsMeasure:
    system_chars: int
    tools_chars: int
    history_chars: int
    tool_result_chars: int
    tool_count: int
    message_count: int
    image_count: int
    body_schema: str


def chars(value) -> int:
    return len(value) if isinstance(value, str) else len(json.dumps(value, separators=(",", ":"), ensure_ascii=False))


def measure(path: str, body: bytes) -> PartsMeasure | None:
    if len(body) > MAX_BODY_BYTES:
        return None
    try:
        data = json.loads(body)
        if not isinstance(data, dict):
            return None
        tools = data.get("tools", [])
        system = history = results = messages = images = 0
        if path in ("/v1/messages", "/v1/ccgpt/messages"):
            schema = "anthropic_messages"
            value = data.get("system", "")
            system = sum(chars(b.get("text", b)) for b in value) if isinstance(value, list) else chars(value)
            for message in data.get("messages", []):
                messages += 1
                content = message.get("content", "")
                if isinstance(content, str):
                    history += len(content)
                    continue
                for block in content:
                    kind = block.get("type")
                    if kind == "tool_result":
                        results += chars(block.get("content", ""))
                    elif kind in ("image", "document"):
                        images += 1
                        history += IMAGE_CHARS
                    elif kind in ("text", "thinking"):
                        history += chars(block.get(kind, ""))
                    else:
                        history += chars(block)
        elif path.startswith(("/v1/responses", "/backend-api/codex/responses")):
            schema = "openai_responses"
            system = chars(data.get("instructions", ""))
            value = data.get("input", [])
            if isinstance(value, str):
                history = len(value)
            else:
                for item in value:
                    messages += 1
                    if item.get("type", "").endswith("_call_output"):
                        results += chars(item.get("output", ""))
                    elif item.get("role") in ("system", "developer"):
                        system += chars(item.get("content", ""))
                    else:
                        content = item.get("content")
                        if isinstance(content, list):
                            image_parts = [p for p in content if p.get("type") == "input_image"]
                            images += len(image_parts)
                            history += chars(item) + sum(IMAGE_CHARS - chars(p) for p in image_parts)
                        else:
                            history += chars(item)
        elif path == "/v1/chat/completions":
            schema = "openai_chat"
            for message in data.get("messages", []):
                messages += 1
                size = chars(message.get("content", ""))
                if message.get("role") in ("system", "developer"):
                    system += size
                elif message.get("role") == "tool":
                    results += size
                else:
                    history += size
        else:
            return None
        return PartsMeasure(system, chars(tools), history, results, len(tools), messages, images, schema)
    except (ValueError, TypeError, AttributeError, RecursionError):
        return None
