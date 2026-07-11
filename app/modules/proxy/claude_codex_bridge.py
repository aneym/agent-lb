from __future__ import annotations

import base64
import json
import math
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import cast

from app.core.anthropic.models import AnthropicMessageRequest
from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonObject, JsonValue
from app.core.utils.sse import format_sse_event, parse_sse_data_json

CCDEX_MODEL = "gpt-5.6-sol"
CCDEX_REASONING_EFFORT = "high"
CCDEX_SERVICE_TIER = "priority"
_SIGNATURE_PREFIX = "codex:"


def claude_to_responses(payload: AnthropicMessageRequest) -> ResponsesRequest:
    """Translate the Claude Messages shapes emitted by Claude Code to Responses input."""
    raw = payload.model_dump(mode="json", exclude_none=True)
    input_items: list[JsonValue] = []

    for message in cast(list[JsonObject], raw.get("messages", [])):
        role_value = message.get("role")
        role = role_value if isinstance(role_value, str) else "user"
        if role == "system":
            role = "user"
        content = message.get("content", "")
        if isinstance(content, str):
            input_items.append(_text_message(role, content))
            continue
        if not isinstance(content, list):
            continue

        message_parts: list[JsonValue] = []
        for raw_part in content:
            if not isinstance(raw_part, dict):
                continue
            part = cast(JsonObject, raw_part)
            part_type = part.get("type")
            if part_type == "text":
                text = part.get("text")
                if isinstance(text, str):
                    message_parts.append({"type": "output_text" if role == "assistant" else "input_text", "text": text})
            elif part_type == "image":
                image_url = _image_data_url(part.get("source"))
                if image_url is not None:
                    message_parts.append({"type": "input_image", "image_url": image_url})
            elif part_type == "thinking" and role == "assistant":
                encrypted = _decode_reasoning_signature(part.get("signature"))
                if encrypted is not None:
                    if message_parts:
                        input_items.append({"type": "message", "role": role, "content": message_parts})
                        message_parts = []
                    input_items.append(
                        {"type": "reasoning", "summary": [], "content": None, "encrypted_content": encrypted}
                    )
            elif part_type == "tool_use":
                if message_parts:
                    input_items.append({"type": "message", "role": role, "content": message_parts})
                    message_parts = []
                call_id = part.get("id")
                name = part.get("name")
                arguments = part.get("input")
                if isinstance(call_id, str) and isinstance(name, str):
                    input_items.append(
                        {
                            "type": "function_call",
                            "call_id": call_id,
                            "name": name,
                            "arguments": json.dumps(
                                arguments if isinstance(arguments, dict) else {}, separators=(",", ":")
                            ),
                        }
                    )
            elif part_type == "tool_result":
                if message_parts:
                    input_items.append({"type": "message", "role": role, "content": message_parts})
                    message_parts = []
                call_id = part.get("tool_use_id")
                if isinstance(call_id, str):
                    input_items.append(
                        {"type": "function_call_output", "call_id": call_id, "output": _tool_result_output(part)}
                    )
        if message_parts:
            input_items.append({"type": "message", "role": role, "content": message_parts})

    tools: list[JsonValue] = []
    for tool in cast(list[JsonObject], raw.get("tools", [])):
        name = tool.get("name")
        parameters = tool.get("input_schema")
        if not isinstance(name, str) or not isinstance(parameters, dict):
            continue
        converted: JsonObject = {"type": "function", "name": name, "parameters": parameters, "strict": False}
        description = tool.get("description")
        if isinstance(description, str):
            converted["description"] = description
        tools.append(converted)

    return ResponsesRequest(
        model=CCDEX_MODEL,
        instructions=_system_text(raw.get("system")),
        input=input_items,
        tools=tools,
        tool_choice=_tool_choice(raw.get("tool_choice")),
        parallel_tool_calls=_parallel_tool_calls(raw.get("tool_choice")),
        reasoning={"effort": CCDEX_REASONING_EFFORT, "summary": "auto"},
        store=False,
        stream=True,
        include=["reasoning.encrypted_content"],
        service_tier=CCDEX_SERVICE_TIER,
    )


def estimate_claude_input_tokens(payload: Mapping[str, JsonValue]) -> int:
    """Return a safe local estimate; this route never calls Anthropic token counting."""
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return max(1, math.ceil(len(encoded) / 4))


async def responses_to_claude_sse(source: AsyncIterator[bytes | str]) -> AsyncIterator[str]:
    state = _ClaudeStreamState()
    buffer = ""
    async for chunk in source:
        buffer += chunk.decode("utf-8", "replace") if isinstance(chunk, bytes) else chunk
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            event = parse_sse_data_json(block)
            if event is None:
                continue
            for translated in state.consume(event):
                yield format_sse_event(translated)
    if buffer.strip():
        event = parse_sse_data_json(buffer)
        if event is not None:
            for translated in state.consume(event):
                yield format_sse_event(translated)
    for translated in state.finish():
        yield format_sse_event(translated)


async def collect_claude_message(source: AsyncIterator[str]) -> JsonObject:
    message: JsonObject = {
        "id": f"msg_{uuid.uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "model": CCDEX_MODEL,
        "content": [],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }
    content = cast(list[JsonObject], message["content"])
    blocks: dict[int, JsonObject] = {}
    async for frame in source:
        event = parse_sse_data_json(frame)
        if event is None:
            continue
        event_type = event.get("type")
        if event_type == "message_start" and isinstance(event.get("message"), dict):
            started = cast(JsonObject, event["message"])
            message["id"] = started.get("id", message["id"])
            message["model"] = started.get("model", CCDEX_MODEL)
        elif event_type == "content_block_start":
            index = event.get("index")
            block = event.get("content_block")
            if isinstance(index, int) and isinstance(block, dict):
                blocks[index] = cast(JsonObject, block)
        elif event_type == "content_block_delta":
            index = event.get("index")
            delta = event.get("delta")
            if not isinstance(index, int) or not isinstance(delta, dict) or index not in blocks:
                continue
            block = blocks[index]
            if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                block["text"] = str(block.get("text", "")) + cast(str, delta["text"])
            elif delta.get("type") == "input_json_delta" and isinstance(delta.get("partial_json"), str):
                block["input"] = str(block.get("input", "")) + cast(str, delta["partial_json"])
        elif event_type == "message_delta":
            delta = event.get("delta")
            usage = event.get("usage")
            if isinstance(delta, dict):
                message["stop_reason"] = delta.get("stop_reason", "end_turn")
            if isinstance(usage, dict):
                message["usage"] = cast(JsonObject, usage)
    for index in sorted(blocks):
        block = blocks[index]
        if block.get("type") == "tool_use" and isinstance(block.get("input"), str):
            try:
                block["input"] = cast(JsonValue, json.loads(cast(str, block["input"])))
            except json.JSONDecodeError:
                block["input"] = {}
        content.append(block)
    return message


def anthropic_error_from_response(status_code: int, body: bytes) -> JsonObject:
    message = body.decode("utf-8", "replace") or f"upstream request failed with status {status_code}"
    try:
        parsed = json.loads(message)
        if isinstance(parsed, dict):
            error = parsed.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                message = error["message"]
    except json.JSONDecodeError:
        pass
    return {"type": "error", "error": {"type": "api_error", "message": message}}


def _text_message(role: str, text: str) -> JsonObject:
    return {
        "type": "message",
        "role": role,
        "content": [{"type": "output_text" if role == "assistant" else "input_text", "text": text}],
    }


def _system_text(value: JsonValue | None) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    return "\n\n".join(
        cast(str, part["text"])
        for part in value
        if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
    )


def _image_data_url(value: JsonValue | None) -> str | None:
    if not isinstance(value, dict):
        return None
    data = value.get("data") or value.get("base64")
    media_type = value.get("media_type") or value.get("mime_type") or "application/octet-stream"
    if not isinstance(data, str) or not isinstance(media_type, str):
        return None
    return f"data:{media_type};base64,{data}"


def _tool_result_output(part: JsonObject) -> JsonValue:
    content = part.get("content", "")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return json.dumps(content, ensure_ascii=False)
    converted: list[JsonValue] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text" and isinstance(item.get("text"), str):
            converted.append({"type": "input_text", "text": item["text"]})
        elif item.get("type") == "image":
            image_url = _image_data_url(item.get("source"))
            if image_url is not None:
                converted.append({"type": "input_image", "image_url": image_url})
    return converted if converted else json.dumps(content, ensure_ascii=False)


def _tool_choice(value: JsonValue | None) -> str | JsonObject | None:
    if not isinstance(value, dict):
        return "auto"
    choice_type = value.get("type")
    if choice_type == "any":
        return "required"
    if choice_type == "none":
        return "none"
    if choice_type == "tool" and isinstance(value.get("name"), str):
        return {"type": "function", "name": value["name"]}
    return "auto"


def _parallel_tool_calls(value: JsonValue | None) -> bool | None:
    if not isinstance(value, dict):
        return None
    disabled = value.get("disable_parallel_tool_use")
    return not disabled if isinstance(disabled, bool) else None


def _encode_reasoning_signature(value: str) -> str:
    encoded = base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")
    return _SIGNATURE_PREFIX + encoded


def _decode_reasoning_signature(value: JsonValue | None) -> str | None:
    if not isinstance(value, str) or not value.startswith(_SIGNATURE_PREFIX):
        return None
    encoded = value.removeprefix(_SIGNATURE_PREFIX)
    try:
        return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return None


@dataclass(slots=True)
class _ClaudeStreamState:
    message_id: str = field(default_factory=lambda: f"msg_{uuid.uuid4().hex}")
    block_index: int = 0
    open_block: str | None = None
    open_call_id: str | None = None
    saw_start: bool = False
    saw_terminal: bool = False
    emitted_text: bool = False
    emitted_tool: bool = False
    actual_model: str = CCDEX_MODEL

    def consume(self, event: JsonObject) -> list[JsonObject]:
        event_type = event.get("type")
        output: list[JsonObject] = []
        if event_type == "response.created":
            response = event.get("response")
            if isinstance(response, dict):
                response_id = response.get("id")
                if isinstance(response_id, str):
                    self.message_id = response_id.replace("resp_", "msg_", 1)
            output.extend(self._start())
        elif event_type == "response.output_text.delta":
            output.extend(self._start())
            output.extend(self._open_text())
            delta = event.get("delta")
            if isinstance(delta, str):
                self.emitted_text = True
                output.append(
                    {
                        "type": "content_block_delta",
                        "index": self.block_index,
                        "delta": {"type": "text_delta", "text": delta},
                    }
                )
        elif event_type == "response.output_item.added":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                output.extend(self._start())
                output.extend(self._close_block())
                call_id = item.get("call_id") or item.get("id")
                name = item.get("name")
                if isinstance(call_id, str) and isinstance(name, str):
                    self.open_block = "tool_use"
                    self.open_call_id = call_id
                    self.emitted_tool = True
                    output.append(
                        {
                            "type": "content_block_start",
                            "index": self.block_index,
                            "content_block": {"type": "tool_use", "id": call_id, "name": name, "input": {}},
                        }
                    )
        elif event_type == "response.function_call_arguments.delta" and self.open_block == "tool_use":
            delta = event.get("delta")
            if isinstance(delta, str):
                output.append(
                    {
                        "type": "content_block_delta",
                        "index": self.block_index,
                        "delta": {"type": "input_json_delta", "partial_json": delta},
                    }
                )
        elif event_type == "response.output_item.done":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                if self.open_block != "tool_use":
                    output.extend(self._start())
                    output.extend(self._close_block())
                    call_id = item.get("call_id") or item.get("id")
                    name = item.get("name")
                    if isinstance(call_id, str) and isinstance(name, str):
                        self.open_block = "tool_use"
                        self.open_call_id = call_id
                        self.emitted_tool = True
                        output.append(
                            {
                                "type": "content_block_start",
                                "index": self.block_index,
                                "content_block": {"type": "tool_use", "id": call_id, "name": name, "input": {}},
                            }
                        )
                        arguments = item.get("arguments")
                        if isinstance(arguments, str) and arguments:
                            output.append(
                                {
                                    "type": "content_block_delta",
                                    "index": self.block_index,
                                    "delta": {"type": "input_json_delta", "partial_json": arguments},
                                }
                            )
                output.extend(self._close_block())
            elif isinstance(item, dict) and item.get("type") == "message" and not self.emitted_text:
                text = _message_output_text(item)
                if text:
                    output.extend(self._start())
                    output.extend(self._open_text())
                    output.append(
                        {
                            "type": "content_block_delta",
                            "index": self.block_index,
                            "delta": {"type": "text_delta", "text": text},
                        }
                    )
                    self.emitted_text = True
        elif event_type in {"response.completed", "response.incomplete"}:
            response = event.get("response")
            output.extend(self._complete(response if isinstance(response, dict) else {}))
        elif event_type in {"response.failed", "error"}:
            output.extend(self._close_block())
            error = event.get("error")
            message = "OpenAI response failed"
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                message = error["message"]
            output.append({"type": "error", "error": {"type": "api_error", "message": message}})
            self.saw_terminal = True
        return output

    def finish(self) -> list[JsonObject]:
        if self.saw_terminal:
            return []
        return self._complete({})

    def _start(self) -> list[JsonObject]:
        if self.saw_start:
            return []
        self.saw_start = True
        return [
            {
                "type": "message_start",
                "message": {
                    "id": self.message_id,
                    "type": "message",
                    "role": "assistant",
                    "model": CCDEX_MODEL,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            }
        ]

    def _open_text(self) -> list[JsonObject]:
        if self.open_block == "text":
            return []
        output = self._close_block()
        self.open_block = "text"
        output.append(
            {"type": "content_block_start", "index": self.block_index, "content_block": {"type": "text", "text": ""}}
        )
        return output

    def _close_block(self) -> list[JsonObject]:
        if self.open_block is None:
            return []
        output = [{"type": "content_block_stop", "index": self.block_index}]
        self.block_index += 1
        self.open_block = None
        self.open_call_id = None
        return output

    def _complete(self, response: Mapping[str, JsonValue]) -> list[JsonObject]:
        if self.saw_terminal:
            return []
        self.saw_terminal = True
        output = self._start()
        output.extend(self._close_block())
        usage = response.get("usage")
        input_tokens = 0
        output_tokens = 0
        cache_tokens = 0
        if isinstance(usage, dict):
            input_tokens = _int_value(usage.get("input_tokens"))
            output_tokens = _int_value(usage.get("output_tokens"))
            details = usage.get("input_tokens_details")
            if isinstance(details, dict):
                cache_tokens = _int_value(details.get("cached_tokens"))
        stop_reason = "tool_use" if self.emitted_tool else "end_turn"
        message_usage: JsonObject = {"input_tokens": input_tokens, "output_tokens": output_tokens}
        if cache_tokens:
            message_usage["cache_read_input_tokens"] = cache_tokens
        output.extend(
            [
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                    "usage": message_usage,
                },
                {"type": "message_stop"},
            ]
        )
        return output


def _message_output_text(item: Mapping[str, JsonValue]) -> str:
    content = item.get("content")
    if not isinstance(content, list):
        return ""
    return "".join(
        cast(str, part["text"])
        for part in content
        if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str)
    )


def _int_value(value: JsonValue | None) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
