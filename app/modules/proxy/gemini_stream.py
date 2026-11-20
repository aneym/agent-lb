from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class GeminiUsage:
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    reasoning_tokens: int


def _count(metadata: dict[str, Any], key: str) -> int:
    value = metadata.get(key)
    return int(value) if isinstance(value, (int, float)) and value > 0 else 0


def usage_from_chunk(chunk: dict[str, Any]) -> GeminiUsage | None:
    metadata = chunk.get("usageMetadata")
    if not isinstance(metadata, dict):
        return None
    thoughts = _count(metadata, "thoughtsTokenCount")
    return GeminiUsage(
        input_tokens=_count(metadata, "promptTokenCount") + _count(metadata, "toolUsePromptTokenCount"),
        output_tokens=_count(metadata, "candidatesTokenCount") + thoughts,
        cached_input_tokens=_count(metadata, "cachedContentTokenCount"),
        reasoning_tokens=thoughts,
    )


def _encode(chunk: dict[str, Any]) -> bytes:
    return json.dumps(chunk, separators=(",", ":"), ensure_ascii=False).encode()


def sse_event(chunk: dict[str, Any]) -> bytes:
    return b"data: " + _encode(chunk) + b"\r\n\r\n"


def json_array_item(chunk: dict[str, Any], *, first: bool) -> bytes:
    return (b"[" if first else b",\r\n") + _encode(chunk)


JSON_ARRAY_END = b"]"
JSON_ARRAY_EMPTY = b"[]"


class GeminiResponseAggregate:
    def __init__(self) -> None:
        self._candidates: dict[int, dict[str, Any]] = {}
        self._top: dict[str, Any] = {}

    def add(self, chunk: dict[str, Any]) -> None:
        for key, value in chunk.items():
            if key != "candidates":
                self._top[key] = value
        candidates = chunk.get("candidates")
        if not isinstance(candidates, list):
            return
        for position, candidate in enumerate(candidates):
            if not isinstance(candidate, dict):
                continue
            index = candidate.get("index", position)
            merged = self._candidates.setdefault(int(index), {"content": {"role": "model", "parts": []}})
            for key, value in candidate.items():
                if key == "content" and isinstance(value, dict):
                    self._merge_content(merged["content"], value)
                else:
                    merged[key] = value

    @staticmethod
    def _merge_content(target: dict[str, Any], content: dict[str, Any]) -> None:
        if isinstance(content.get("role"), str):
            target["role"] = content["role"]
        parts = content.get("parts")
        if not isinstance(parts, list):
            return
        for part in parts:
            if not isinstance(part, dict):
                continue
            previous = target["parts"][-1] if target["parts"] else None
            if (
                previous is not None
                and set(part) == {"text"}
                and set(previous) == {"text"}
                and isinstance(part["text"], str)
            ):
                previous["text"] += part["text"]
            else:
                target["parts"].append(dict(part))

    def response(self) -> dict[str, Any]:
        body = dict(self._top)
        body["candidates"] = [self._candidates[index] for index in sorted(self._candidates)]
        return body
