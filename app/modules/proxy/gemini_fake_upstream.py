from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

from app.modules.proxy.gemini_upstream import GeminiUpstreamCall, GeminiUpstreamError

_TOOL_TRIGGER = "use a tool"
_PREFERRED_TOOLS = ("list_dir", "list_directory", "ls", "view_file", "read_file", "glob", "find_by_name")
_CHUNK_DELAY_SECONDS = 0.05


def _parts(content: object) -> list[dict[str, Any]]:
    if not isinstance(content, dict):
        return []
    parts = content.get("parts")
    return [part for part in parts if isinstance(part, dict)] if isinstance(parts, list) else []


def _last_turn(body: dict[str, Any]) -> list[dict[str, Any]]:
    contents = body.get("contents")
    if not isinstance(contents, list) or not contents:
        return []
    return _parts(contents[-1])


def _all_text(body: dict[str, Any]) -> str:
    contents = body.get("contents")
    if not isinstance(contents, list):
        return ""
    return " ".join(
        part["text"]
        for content in contents
        if isinstance(content, dict) and content.get("role") == "user"
        for part in _parts(content)
        if isinstance(part.get("text"), str)
    )


def _declarations(body: dict[str, Any]) -> list[dict[str, Any]]:
    tools = body.get("tools")
    if not isinstance(tools, list):
        return []
    return [
        declaration
        for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("functionDeclarations"), list)
        for declaration in tool["functionDeclarations"]
        if isinstance(declaration, dict) and isinstance(declaration.get("name"), str)
    ]


def _schema_of(declaration: dict[str, Any]) -> dict[str, Any]:
    for key in ("parameters", "parametersJsonSchema"):
        schema = declaration.get(key)
        if isinstance(schema, dict):
            return schema
    return {}


def _fill_args(schema: dict[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties")
    required = schema.get("required")
    if not isinstance(properties, dict) or not isinstance(required, list):
        return {}
    args: dict[str, Any] = {}
    for name in required:
        spec = properties.get(name)
        kind = str(spec.get("type", "string")).lower() if isinstance(spec, dict) else "string"
        if kind in ("integer", "number"):
            args[name] = 1
        elif kind == "boolean":
            args[name] = False
        elif kind == "array":
            args[name] = []
        elif kind == "object":
            args[name] = {}
        else:
            args[name] = "."
    return args


def _pick_tool(body: dict[str, Any]) -> dict[str, Any] | None:
    declarations = _declarations(body)
    for preferred in _PREFERRED_TOOLS:
        for declaration in declarations:
            if declaration["name"].lower() == preferred:
                return declaration
    return declarations[0] if declarations else None


def _chunk(parts: list[dict[str, Any]], *, finish: bool, usage: dict[str, int] | None) -> dict[str, Any]:
    candidate: dict[str, Any] = {"content": {"role": "model", "parts": parts}, "index": 0}
    if finish:
        candidate["finishReason"] = "STOP"
    chunk: dict[str, Any] = {"candidates": [candidate], "modelVersion": "gemini-4-neon-fake"}
    if usage is not None:
        chunk["usageMetadata"] = usage
    return chunk


def _usage(body: dict[str, Any], output_tokens: int) -> dict[str, int]:
    prompt_tokens = max(1, len(json.dumps(body.get("contents", []))) // 4)
    thoughts = 3
    return {
        "promptTokenCount": prompt_tokens,
        "candidatesTokenCount": output_tokens,
        "thoughtsTokenCount": thoughts,
        "totalTokenCount": prompt_tokens + output_tokens + thoughts,
    }


class FakeGeminiUpstream:
    def stream(self, call: GeminiUpstreamCall) -> AsyncGenerator[dict[str, Any], None]:
        return self._reply(call)

    async def _reply(self, call: GeminiUpstreamCall) -> AsyncGenerator[dict[str, Any], None]:
        body = call.body
        if "fake-gemini-429" in _all_text(body):
            raise GeminiUpstreamError(429, "Fake upstream rate limit", kind="rate_limit")
        last = _last_turn(body)
        responses = [part["functionResponse"] for part in last if isinstance(part.get("functionResponse"), dict)]
        if responses:
            names = ", ".join(str(response.get("name", "?")) for response in responses)
            words = ["Fake gateway ", f"received the result of {names}. ", "Tool round trip ", "complete."]
        else:
            tool = _pick_tool(body) if _TOOL_TRIGGER in _all_text(body).lower() else None
            if tool is not None:
                call_part = {"functionCall": {"name": tool["name"], "args": _fill_args(_schema_of(tool))}}
                yield _chunk([{"text": "Calling a tool."}], finish=False, usage=None)
                await asyncio.sleep(_CHUNK_DELAY_SECONDS)
                yield _chunk([call_part], finish=True, usage=_usage(body, 9))
                return
            words = ["pong ", "from the ", "agent-lb fake ", "Gemini upstream."]
        for index, word in enumerate(words):
            last_chunk = index == len(words) - 1
            yield _chunk([{"text": word}], finish=last_chunk, usage=_usage(body, len(words)) if last_chunk else None)
            await asyncio.sleep(_CHUNK_DELAY_SECONDS)
