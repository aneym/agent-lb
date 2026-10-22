from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import cast

from app.core.types import JsonObject, JsonValue
from app.core.utils.sse import format_sse_event, parse_sse_data_json
from app.modules.proxy.claude_codex_bridge import CCGPT_MODEL

_PROMPT_TOO_LONG = "Prompt is too long"


_CONTEXT_OVERFLOW_CODE = "context_length_exceeded"


_CONTEXT_OVERFLOW_MESSAGE_MARKERS = (
    "prompt is too long",
    "context window",
    "context length",
    "input token limit",
    "maximum context length",
)


def _is_context_overflow_error(code: str | None, message: str | None) -> bool:
    if isinstance(code, str) and code.strip().lower() == _CONTEXT_OVERFLOW_CODE:
        return True
    if isinstance(message, str):
        lowered = message.lower()
        return any(marker in lowered for marker in _CONTEXT_OVERFLOW_MESSAGE_MARKERS)
    return False


def _anthropic_overflow_message(detail: str | None) -> str:
    text = detail.strip() if isinstance(detail, str) and detail.strip() else ""
    if not text:
        return _PROMPT_TOO_LONG
    if "prompt is too long" in text.lower():
        return text
    return f"{_PROMPT_TOO_LONG}: {text}"


def _anthropic_error_payload(code: str | None, message: str) -> JsonObject:
    if _is_context_overflow_error(code, message):
        return {"type": "invalid_request_error", "message": _anthropic_overflow_message(message)}
    return {"type": "api_error", "message": message}


def _error_fields_from_event(event: JsonObject) -> tuple[str | None, str]:

    error = event.get("error")
    if not isinstance(error, dict):
        response = event.get("response")
        error = response.get("error") if isinstance(response, dict) else None
    if not isinstance(error, dict) and event.get("type") == "error":
        error = event
    message = "OpenAI response failed"
    code: str | None = None
    if isinstance(error, dict):
        message_value = error.get("message")
        if isinstance(message_value, str) and message_value.strip():
            message = message_value
        code_value = error.get("code")
        if isinstance(code_value, str) and code_value.strip():
            code = code_value
    return code, message


async def responses_to_claude_events(
    source: AsyncIterator[bytes | str], model: str = CCGPT_MODEL, input_tokens_estimate: int = 0
) -> AsyncIterator[JsonObject]:

    state = _ClaudeStreamState(actual_model=model, input_tokens_estimate=input_tokens_estimate)
    buffer = ""
    async for chunk in source:
        buffer += chunk.decode("utf-8", "replace") if isinstance(chunk, bytes) else chunk
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            event = parse_sse_data_json(block)
            if event is None:
                continue
            for translated in state.consume(event):
                yield translated
    if buffer.strip():
        event = parse_sse_data_json(buffer)
        if event is not None:
            for translated in state.consume(event):
                yield translated
    for translated in state.finish():
        yield translated


async def responses_to_claude_sse(source: AsyncIterator[bytes | str]) -> AsyncIterator[str]:
    async for event in responses_to_claude_events(source):
        yield format_sse_event(event)


async def format_claude_events(events: AsyncIterator[JsonObject]) -> AsyncIterator[str]:
    async for event in events:
        yield format_sse_event(event)


_ERROR_STATUS_BY_TYPE: Mapping[str, int] = {
    "invalid_request_error": 400,
    "authentication_error": 401,
    "permission_error": 403,
    "not_found_error": 404,
    "rate_limit_error": 429,
    "overloaded_error": 529,
    "api_error": 502,
}


def anthropic_status_for_error(error_frame: JsonObject) -> int:

    error = error_frame.get("error")
    error_type = error.get("type") if isinstance(error, dict) else None
    if isinstance(error_type, str):
        return _ERROR_STATUS_BY_TYPE.get(error_type, 502)
    return 502


async def collect_claude_message(source: AsyncIterator[str]) -> JsonObject:
    message: JsonObject = {
        "id": f"msg_{uuid.uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "model": CCGPT_MODEL,
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
            message["model"] = started.get("model", CCGPT_MODEL)
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
    code: str | None = None
    try:
        parsed = json.loads(message)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, dict):
            if isinstance(error.get("message"), str):
                message = error["message"]
            if isinstance(error.get("code"), str):
                code = error["code"]
    return {"type": "error", "error": _anthropic_error_payload(code, message)}


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
    actual_model: str = CCGPT_MODEL

    input_tokens_estimate: int = 0

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
            code, message = _error_fields_from_event(event)
            output.append({"type": "error", "error": _anthropic_error_payload(code, message)})
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
                    "model": self.actual_model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": self.input_tokens_estimate, "output_tokens": 0},
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

        message_usage: JsonObject = {
            "input_tokens": max(0, input_tokens - cache_tokens),
            "cache_read_input_tokens": cache_tokens,
            "cache_creation_input_tokens": 0,
            "output_tokens": output_tokens,
        }
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
