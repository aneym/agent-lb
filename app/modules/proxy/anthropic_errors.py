from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi.responses import JSONResponse

from app.core.anthropic.models import AnthropicMessageRequest
from app.modules.api_keys.service_data import (
    API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET,
    ApiKeyRequestUsageBudget,
    ApiKeyUsageReservationData,
)
from app.modules.proxy.anthropic_proxy_error import AnthropicProxyError
from app.modules.proxy.reservations import release_reservation


def openai_error_type_for_status(status_code: int) -> str:
    if status_code == 401:
        return "authentication_error"
    if status_code == 403:
        return "permission_error"
    if status_code == 429:
        return "rate_limit_error"
    if status_code < 500:
        return "invalid_request_error"
    return "server_error"


def estimate_anthropic_request_usage(
    payload: AnthropicMessageRequest, *, raw_body: bytes | None = None
) -> ApiKeyRequestUsageBudget:

    if raw_body is not None:
        size = len(raw_body)
    else:
        data = payload.model_dump(mode="json", exclude_none=True)
        data.pop("model", None)
        data.pop("max_tokens", None)
        data.pop("stream", None)
        serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)
        size = len(serialized.encode("utf-8"))
    return ApiKeyRequestUsageBudget(
        input_tokens=min(size, API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET),
        output_tokens=payload.max_tokens,
    )


def anthropic_error_response(
    status_code: int,
    error_type: str,
    message: str,
    *,
    retry_at: int | None = None,
    details: Any | None = None,
    code: str | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {
        "type": error_type,
        "message": message,
    }
    if code is not None:
        error["code"] = code

    if details is not None:
        error["details"] = details
    return JSONResponse(
        status_code=status_code,
        content={
            "type": "error",
            "error": error,
        },
        headers=retry_headers(retry_at),
    )


def retry_headers(retry_at: int | None) -> dict[str, str] | None:
    if retry_at is None:
        return None
    return {
        "retry-after": str(max(0, int(retry_at) - int(time.time()))),
        "anthropic-ratelimit-unified-reset": str(int(retry_at)),
    }


def anthropic_proxy_error_response(exc: AnthropicProxyError) -> JSONResponse:

    if exc.retry_at is not None or exc.status_code == 429:
        return anthropic_error_response(429, "rate_limit_error", exc.message, retry_at=exc.retry_at)
    response = anthropic_error_response(exc.status_code, exc.code, exc.message, details=exc.details)
    if exc.code == "balance_exhausted":
        response.headers["x-should-retry"] = "false"
    return response


async def collect_anthropic_body(body: AsyncIterator[bytes]) -> bytes:
    chunks = bytearray()
    async for chunk in body:
        chunks.extend(chunk)
    return bytes(chunks)


async def anthropic_stream_error_guard(
    body: AsyncIterator[bytes],
    *,
    streaming: bool,
    api_key_reservation: ApiKeyUsageReservationData | None = None,
) -> AsyncIterator[bytes]:

    try:
        async for chunk in body:
            yield chunk
    except AnthropicProxyError as exc:
        await release_reservation(api_key_reservation)
        error_type = "rate_limit_error" if (exc.retry_at is not None or exc.status_code == 429) else exc.code
        error_body: dict[str, Any] = {"type": error_type, "message": exc.message}
        if exc.details is not None:
            error_body["details"] = exc.details
        envelope = json.dumps(
            {"type": "error", "error": error_body},
            ensure_ascii=False,
        )
        if streaming:
            yield f"event: error\ndata: {envelope}\n\n".encode()
        else:
            yield envelope.encode()
