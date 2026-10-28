from __future__ import annotations

import json
from collections.abc import AsyncIterator

from app.core.openai.models import OpenAIError, OpenAIErrorEnvelope
from app.core.upstream_capacity import is_capacity_error

RETRYABLE_CODE = "server_error"
_FAST_MARKERS = ("overloaded", "capacity", "slow_down")


def retryable_overload_event(block: str) -> str:
    lowered = block.lower()
    if not any(marker in lowered for marker in _FAST_MARKERS):
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
    if not isinstance(error, dict) or not is_capacity_error(error.get("code"), error.get("message")):
        return block
    error["code"] = RETRYABLE_CODE
    error["type"] = RETRYABLE_CODE
    if payload.get("status") == 429:
        payload["status"] = 503
    rewritten = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return f"{prefix}{marker}{rewritten}{data[len(body) :]}"


async def retryable_overload_stream(stream: AsyncIterator[str]) -> AsyncIterator[str]:
    async for block in stream:
        yield retryable_overload_event(block)


def retryable_overload_status(status_code: int, envelope: OpenAIErrorEnvelope) -> tuple[int, OpenAIErrorEnvelope]:
    error = envelope.error
    if error is None or not is_capacity_error(error.code, error.message):
        return status_code, envelope
    retryable = OpenAIError.model_construct(message=error.message, type=RETRYABLE_CODE, code=RETRYABLE_CODE)
    return 503, OpenAIErrorEnvelope.model_construct(error=retryable)
