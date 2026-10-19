from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.core.openai.chat_models import (
    ChatChunkChoice,
    ChatChunkDelta,
    ChatCompletion,
    ChatCompletionChoice,
    ChatCompletionChunk,
    ChatCompletionMessage,
    ChatCompletionResult,
    ChatCompletionTokensDetails,
    ChatCompletionUsage,
    ChatPromptTokensDetails,
)
from app.core.openai.chat_tool_calls import (
    ToolCallIndex,
    ToolCallState,
    _as_mapping,
    _compact_tool_calls,
    _merge_tool_call_delta,
    _tool_call_delta_from_payload,
)
from app.core.openai.models import OpenAIError, OpenAIErrorEnvelope, ResponseUsage
from app.core.types import JsonValue
from app.core.utils.sse import format_sse_data, parse_sse_data_json


@dataclass
class _ChatChunkState:
    tool_index: ToolCallIndex = field(default_factory=ToolCallIndex)
    tool_calls: list["ToolCallState"] = field(default_factory=list)
    saw_tool_call: bool = False
    sent_role: bool = False


def _parse_data(line: str) -> dict[str, JsonValue] | None:
    return parse_sse_data_json(line)


def iter_chat_chunks(
    lines: Iterable[str],
    model: str,
    *,
    created: int | None = None,
    state: _ChatChunkState | None = None,
    include_usage: bool = False,
) -> Iterable[str]:
    created = created or int(time.time())
    state = state or _ChatChunkState()
    for line in lines:
        payload = _parse_data(line)
        if not payload:
            continue
        event_type = payload.get("type")
        if event_type in ("response.output_text.delta", "response.refusal.delta"):
            delta_text = payload.get("delta")
            role = None
            if not state.sent_role:
                role = "assistant"
            if event_type == "response.refusal.delta":
                delta_obj = ChatChunkDelta(
                    role=role,
                    refusal=delta_text if isinstance(delta_text, str) else None,
                )
            else:
                delta_obj = ChatChunkDelta(
                    role=role,
                    content=delta_text if isinstance(delta_text, str) else None,
                )
            chunk = ChatCompletionChunk(
                id="chatcmpl_temp",
                created=created,
                model=model,
                choices=[
                    ChatChunkChoice(
                        index=0,
                        delta=delta_obj,
                        finish_reason=None,
                    )
                ],
            )
            yield _dump_chunk(chunk, include_usage=include_usage)
            if role is not None:
                state.sent_role = True
        tool_delta = _tool_call_delta_from_payload(payload, state.tool_index)
        if tool_delta is not None:
            tool_state = _merge_tool_call_delta(state.tool_calls, tool_delta)
            stream_delta = tool_state.build_stream_delta()
            if stream_delta is not None:
                state.saw_tool_call = True
                role = None
                if not state.sent_role:
                    role = "assistant"
                chunk = ChatCompletionChunk(
                    id="chatcmpl_temp",
                    created=created,
                    model=model,
                    choices=[
                        ChatChunkChoice(
                            index=0,
                            delta=ChatChunkDelta(
                                role=role,
                                tool_calls=[stream_delta.to_chunk_call()],
                            ),
                            finish_reason=None,
                        )
                    ],
                )
                yield _dump_chunk(chunk, include_usage=include_usage)
                if role is not None:
                    state.sent_role = True
        if event_type in ("response.failed", "error"):
            error = None
            if event_type == "response.failed":
                response = payload.get("response")
                if isinstance(response, dict):
                    maybe_error = response.get("error")
                    if isinstance(maybe_error, dict):
                        error = maybe_error
            else:
                maybe_error = payload.get("error")
                if isinstance(maybe_error, dict):
                    error = maybe_error
            if error is not None:
                error_payload: dict[str, JsonValue] = {"error": error}
                yield _dump_sse(error_payload)
                yield "data: [DONE]\n\n"
                return
        if event_type in ("response.completed", "response.incomplete"):
            for tool_state in state.tool_calls:
                stream_delta = tool_state.build_stream_delta()
                if stream_delta is None:
                    continue
                state.saw_tool_call = True
                role = None
                if not state.sent_role:
                    role = "assistant"
                chunk = ChatCompletionChunk(
                    id="chatcmpl_temp",
                    created=created,
                    model=model,
                    choices=[
                        ChatChunkChoice(
                            index=0,
                            delta=ChatChunkDelta(
                                role=role,
                                tool_calls=[stream_delta.to_chunk_call()],
                            ),
                            finish_reason=None,
                        )
                    ],
                )
                yield _dump_chunk(chunk, include_usage=include_usage)
                if role is not None:
                    state.sent_role = True
            usage = None
            if include_usage:
                response = payload.get("response")
                if isinstance(response, dict):
                    usage = _map_usage(_parse_usage(response.get("usage")))
            finish_reason = "tool_calls" if state.saw_tool_call else "stop"
            if event_type == "response.incomplete" and not state.saw_tool_call:
                finish_reason = _finish_reason_from_incomplete(payload.get("response"))
            done = ChatCompletionChunk(
                id="chatcmpl_temp",
                created=created,
                model=model,
                choices=[
                    ChatChunkChoice(
                        index=0,
                        delta=ChatChunkDelta(),
                        finish_reason=finish_reason,
                    )
                ],
            )
            yield _dump_chunk(done, include_usage=include_usage)
            if include_usage:
                usage_chunk = ChatCompletionChunk(
                    id="chatcmpl_temp",
                    created=created,
                    model=model,
                    choices=[],
                    usage=usage,
                )
                yield _dump_chunk(usage_chunk, include_usage=include_usage)
            yield "data: [DONE]\n\n"
            return


async def stream_chat_chunks(
    stream: AsyncIterator[str],
    model: str,
    *,
    include_usage: bool = False,
) -> AsyncIterator[str]:
    created = int(time.time())
    state = _ChatChunkState()
    terminal_chunk_sent = False
    async for line in stream:
        if terminal_chunk_sent:
            continue
        for chunk in iter_chat_chunks(
            [line],
            model=model,
            created=created,
            state=state,
            include_usage=include_usage,
        ):
            yield chunk
            if chunk.strip() == "data: [DONE]":
                terminal_chunk_sent = True
                break


async def collect_chat_completion(stream: AsyncIterator[str], model: str) -> ChatCompletionResult:
    created = int(time.time())
    content_parts: list[str] = []
    refusal_parts: list[str] = []
    response_id: str | None = None
    usage: ResponseUsage | None = None
    incomplete_reason: str | None = None
    tool_index = ToolCallIndex()
    tool_calls: list[ToolCallState] = []

    async for line in stream:
        payload = _parse_data(line)
        if not payload:
            continue
        event_type = payload.get("type")
        if event_type == "response.output_text.delta":
            delta = payload.get("delta")
            if isinstance(delta, str):
                content_parts.append(delta)
        if event_type == "response.refusal.delta":
            delta = payload.get("delta")
            if isinstance(delta, str):
                refusal_parts.append(delta)
        tool_delta = _tool_call_delta_from_payload(payload, tool_index)
        if tool_delta is not None:
            _merge_tool_call_delta(tool_calls, tool_delta)
        if event_type in ("response.failed", "error"):
            error = None
            if event_type == "response.failed":
                response = payload.get("response")
                if isinstance(response, dict):
                    maybe_error = response.get("error")
                    if isinstance(maybe_error, dict):
                        error = maybe_error
            else:
                maybe_error = payload.get("error")
                if isinstance(maybe_error, dict):
                    error = maybe_error
            if error is not None:
                return _error_envelope_from_payload(error)
            return _default_error_envelope()
        if event_type in ("response.completed", "response.incomplete"):
            response = payload.get("response")
            if isinstance(response, dict):
                response_id_value = response.get("id")
                if isinstance(response_id_value, str):
                    response_id = response_id_value
                usage = _parse_usage(response.get("usage"))
                if event_type == "response.incomplete":
                    incomplete_reason = _finish_reason_from_incomplete(response)

    message_content: str | None = "".join(content_parts)
    message_refusal = "".join(refusal_parts) or None
    message_tool_calls = _compact_tool_calls(tool_calls)
    has_tool_calls = bool(message_tool_calls)
    finish_reason = "tool_calls" if has_tool_calls else (incomplete_reason or "stop")
    if (has_tool_calls or message_refusal) and not message_content:
        message_content = None
    message = ChatCompletionMessage(
        role="assistant",
        content=message_content,
        refusal=message_refusal,
        tool_calls=message_tool_calls or None,
    )
    choice = ChatCompletionChoice(
        index=0,
        message=message,
        finish_reason=finish_reason,
    )
    completion = ChatCompletion(
        id=response_id or "chatcmpl_temp",
        created=created,
        model=model,
        choices=[choice],
        usage=_map_usage(usage),
    )
    return completion


def _map_usage(usage: ResponseUsage | None) -> ChatCompletionUsage | None:
    if usage is None:
        return None
    prompt_tokens = usage.input_tokens
    completion_tokens = usage.output_tokens
    total_tokens = usage.total_tokens
    if prompt_tokens is None and completion_tokens is None and total_tokens is None:
        return None
    prompt_details = None
    cached_tokens = usage.input_tokens_details.cached_tokens if usage.input_tokens_details else None
    if cached_tokens is not None:
        prompt_details = ChatPromptTokensDetails(cached_tokens=cached_tokens)

    completion_details = None
    reasoning_tokens = usage.output_tokens_details.reasoning_tokens if usage.output_tokens_details else None
    if reasoning_tokens is not None:
        completion_details = ChatCompletionTokensDetails(reasoning_tokens=reasoning_tokens)
    return ChatCompletionUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        prompt_tokens_details=prompt_details,
        completion_tokens_details=completion_details,
    )


def _parse_usage(value: JsonValue) -> ResponseUsage | None:
    if not isinstance(value, dict):
        return None
    try:
        return ResponseUsage.model_validate(value)
    except ValidationError:
        return None


def _dump_chunk(chunk: ChatCompletionChunk, *, include_usage: bool = False) -> str:
    payload = chunk.model_dump(mode="json", exclude_none=True)
    if include_usage and "usage" not in payload:
        payload["usage"] = None
    return _dump_sse(payload)


def _dump_sse(payload: dict[str, JsonValue]) -> str:
    return format_sse_data(payload)


def _finish_reason_from_incomplete(response: JsonValue | None) -> str:
    response_mapping = _as_mapping(response)
    if response_mapping is None:
        return "stop"
    details = _as_mapping(response_mapping.get("incomplete_details"))
    if details is not None:
        reason = details.get("reason")
        if reason in ("max_output_tokens", "max_tokens"):
            return "length"
        if reason == "content_filter":
            return "content_filter"
    return "stop"


def _default_error_envelope() -> OpenAIErrorEnvelope:
    return OpenAIErrorEnvelope(
        error=OpenAIError(
            message="Upstream error",
            type="server_error",
            code="upstream_error",
        )
    )


def _error_envelope_from_payload(payload: Mapping[str, JsonValue]) -> OpenAIErrorEnvelope:
    normalized = _normalize_error_payload(payload)
    if not normalized:
        return _default_error_envelope()
    try:
        error = OpenAIError.model_validate(normalized)
    except ValidationError:
        return _default_error_envelope()
    return OpenAIErrorEnvelope(error=error)


def _normalize_error_payload(payload: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    normalized: dict[str, JsonValue] = {}
    for key in ("message", "type", "code", "param", "plan_type"):
        value = payload.get(key)
        if isinstance(value, str):
            normalized[key] = value
    for key in ("resets_at", "resets_in_seconds"):
        value = payload.get(key)
        number = _coerce_number(value)
        if number is not None:
            normalized[key] = number
    return normalized


def _coerce_number(value: JsonValue) -> int | float | None:
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None
