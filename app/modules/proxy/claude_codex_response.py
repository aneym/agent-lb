from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast

from app.core.types import JsonObject, JsonValue

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


def anthropic_error_payload(code: str | None, message: str) -> JsonObject:
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


@dataclass(slots=True)
class ClaudeStreamState:
    actual_model: str
    message_id: str = field(default_factory=lambda: f"msg_{uuid.uuid4().hex}")
    block_index: int = 0
    open_block: str | None = None
    open_call_id: str | None = None
    saw_start: bool = False
    saw_terminal: bool = False
    emitted_text: bool = False
    emitted_tool: bool = False
    emitted_arguments: bool = False

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
            if isinstance(delta, str) and delta:
                self.emitted_arguments = True
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
                if (
                    self.open_block == "tool_use"
                    and not self.emitted_arguments
                    and isinstance(arguments, str)
                    and arguments
                ):
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
            output.append({"type": "error", "error": anthropic_error_payload(code, message)})
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
        self.emitted_arguments = False
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
