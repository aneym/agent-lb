from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Mapping

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.clients.proxy import ProxyResponseError
from app.core.openai.chat_requests import ChatCompletionsRequest
from app.core.openai.chat_responses import ChatCompletion, ChatCompletionUsage
from app.core.openai.models import OpenAIErrorEnvelope as OpenAIErrorEnvelopeModel
from app.core.types import JsonValue
from app.modules.api_keys.service_data import ApiKeyData
from app.modules.proxy.constants import CURSOR_CONTEXT_LIMIT_SYNTHETIC_USAGE_TOKENS, logger
from app.modules.proxy.response_errors import error_details_from_content


def is_cursor_compat_client(request: Request, api_key: ApiKeyData | None) -> bool:
    if api_key is not None and api_key.name.strip().lower() == "cursor":
        return True
    user_agent = request.headers.get("user-agent", "")
    return "cursor" in user_agent.lower()


def is_context_length_startup_error(error: ProxyResponseError | OpenAIErrorEnvelopeModel) -> bool:
    code, message = startup_error_details(error)
    if code == "context_length_exceeded":
        return True
    if message is None:
        return False
    normalized = message.lower()
    return (
        "context window" in normalized
        or "input token limit exceeded" in normalized
        or "token limit exceeded" in normalized
    )


def startup_error_details(error: ProxyResponseError | OpenAIErrorEnvelopeModel) -> tuple[str | None, str | None]:
    if isinstance(error, ProxyResponseError):
        return error_details_from_content(error.payload)
    return error_details_from_content(error)


def cursor_context_limit_usage_stream(
    payload: ChatCompletionsRequest,
    *,
    headers: Mapping[str, str] | None = None,
) -> StreamingResponse:

    response_id = f"chatcmpl_{time.time_ns()}"
    created = int(time.time())
    model = payload.model
    usage_tokens = CURSOR_CONTEXT_LIMIT_SYNTHETIC_USAGE_TOKENS

    def sse_data(data: dict[str, JsonValue] | str) -> str:
        if data == "[DONE]":
            return "data: [DONE]\n\n"
        return f"data: {json.dumps(data, separators=(',', ':'))}\n\n"

    async def body() -> AsyncIterator[str]:
        yield sse_data(
            {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant"},
                        "finish_reason": None,
                    }
                ],
            }
        )
        yield sse_data(
            {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop",
                    }
                ],
            }
        )
        yield sse_data(
            {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [],
                "usage": {
                    "prompt_tokens": usage_tokens,
                    "completion_tokens": 0,
                    "total_tokens": usage_tokens,
                },
            }
        )
        yield sse_data("[DONE]")

    return StreamingResponse(body(), media_type="text/event-stream", headers=headers)


def cursor_context_limit_usage_completion(
    payload: ChatCompletionsRequest,
    *,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    response_id = f"chatcmpl_{time.time_ns()}"
    created = int(time.time())
    model = payload.model
    usage_tokens = CURSOR_CONTEXT_LIMIT_SYNTHETIC_USAGE_TOKENS
    return JSONResponse(
        content={
            "id": response_id,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": ""},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": usage_tokens,
                "completion_tokens": 0,
                "total_tokens": usage_tokens,
            },
        },
        status_code=200,
        headers=headers,
    )


async def stream_with_cursor_usage_fallback(
    stream: AsyncIterator[str],
    payload: ChatCompletionsRequest,
) -> AsyncIterator[str]:
    prompt_tokens = estimate_cursor_prompt_tokens(payload)
    completion_chars = 0
    async for line in stream:
        parsed = parse_chat_completion_sse(line)
        if parsed is None:
            yield line
            continue
        completion_chars += chat_completion_delta_chars(parsed)
        if is_chat_completion_usage_chunk(parsed) and needs_cursor_usage_fallback(parsed.get("usage")):
            completion_tokens = max(1, estimate_tokens_from_chars(completion_chars))
            parsed["usage"] = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            }
            logger.info(
                "cursor_usage_fallback source=stream model=%s prompt_tokens=%s completion_tokens=%s",
                payload.model,
                prompt_tokens,
                completion_tokens,
            )
            yield f"data: {json.dumps(parsed, separators=(',', ':'))}\n\n"
            continue
        yield line


def is_chat_completion_usage_chunk(payload: dict[str, JsonValue]) -> bool:
    return payload.get("choices") == []


def parse_chat_completion_sse(line: str) -> dict[str, JsonValue] | None:
    stripped = line.strip()
    if not stripped.startswith("data:"):
        return None
    data = stripped.removeprefix("data:").strip()
    if data == "[DONE]":
        return None
    try:
        parsed = json.loads(data)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def needs_cursor_usage_fallback(usage: JsonValue) -> bool:
    if not isinstance(usage, dict):
        return True
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    return not isinstance(prompt_tokens, int) or prompt_tokens <= 0 or not isinstance(completion_tokens, int)


def apply_cursor_usage_fallback(
    result: ChatCompletion,
    payload: ChatCompletionsRequest,
    *,
    source: str,
) -> None:
    usage = result.usage.model_dump(mode="json", exclude_none=True) if result.usage is not None else None
    if not needs_cursor_usage_fallback(usage):
        return
    prompt_tokens = estimate_cursor_prompt_tokens(payload)
    completion_tokens = max(1, estimate_tokens_from_chars(chat_completion_result_chars(result)))
    result.usage = ChatCompletionUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    logger.info(
        "cursor_usage_fallback source=%s model=%s prompt_tokens=%s completion_tokens=%s",
        source,
        payload.model,
        prompt_tokens,
        completion_tokens,
    )


def estimate_cursor_prompt_tokens(payload: ChatCompletionsRequest) -> int:
    data = payload.model_dump(mode="json", exclude_none=True)
    counted: dict[str, JsonValue] = {}
    for key in ("messages", "input", "instructions", "tools", "tool_choice", "response_format"):
        value = data.get(key)
        if value is not None:
            counted[key] = value
    message_count = len(data.get("messages", [])) if isinstance(data.get("messages"), list) else 0
    return max(1, estimate_tokens_from_chars(json_text_chars(counted)) + message_count * 4)


def estimate_tokens_from_chars(chars: int) -> int:
    return (max(0, chars) + 3) // 4


def json_text_chars(value: JsonValue) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(json_text_chars(item) for item in value)
    if isinstance(value, dict):
        return sum(json_text_chars(item) for item in value.values())
    return 0


def chat_completion_delta_chars(payload: dict[str, JsonValue]) -> int:
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return 0
    total = 0
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            continue
        for key in ("content", "refusal"):
            value = delta.get(key)
            if isinstance(value, str):
                total += len(value)
        tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list):
            total += json_text_chars(tool_calls)
    return total


def chat_completion_result_chars(result: ChatCompletion) -> int:
    total = 0
    for choice in result.choices:
        message = choice.message
        if isinstance(message.content, str):
            total += len(message.content)
        if isinstance(message.refusal, str):
            total += len(message.refusal)
        if message.tool_calls:
            total += json_text_chars(
                [tool_call.model_dump(mode="json", exclude_none=True) for tool_call in message.tool_calls]
            )
    return total
