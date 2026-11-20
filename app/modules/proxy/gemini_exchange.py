from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from anyio import CancelScope

from app.modules.proxy.gemini_errors import GeminiGatewayError, gemini_error_body, gemini_error_sse_event
from app.modules.proxy.gemini_gateway import GeminiAttempt, GeminiGateway
from app.modules.proxy.gemini_stream import (
    JSON_ARRAY_EMPTY,
    JSON_ARRAY_END,
    GeminiResponseAggregate,
    GeminiUsage,
    json_array_item,
    sse_event,
    usage_from_chunk,
)
from app.modules.proxy.gemini_upstream import GeminiUpstreamError


def _stream_failure() -> GeminiUpstreamError:
    return GeminiUpstreamError(502, "The Gemini upstream stream failed.", kind="transient")


async def _chunks(attempt: GeminiAttempt) -> AsyncIterator[dict[str, Any]]:
    if attempt.first_chunk is not None:
        yield attempt.first_chunk
    async for chunk in attempt.rest:
        yield chunk


async def stream_bytes(gateway: GeminiGateway, attempt: GeminiAttempt, *, sse: bool) -> AsyncIterator[bytes]:
    usage: GeminiUsage | None = None
    error: GeminiUpstreamError | None = None
    cancelled = False
    first = True
    try:
        async for chunk in _chunks(attempt):
            usage = usage_from_chunk(chunk) or usage
            yield sse_event(chunk) if sse else json_array_item(chunk, first=first)
            first = False
    except (asyncio.CancelledError, GeneratorExit):
        cancelled = True
        raise
    except Exception as exc:
        error = exc if isinstance(exc, GeminiUpstreamError) else _stream_failure()
        failure = GeminiGatewayError(error.status_code, error.message, code=f"upstream_{error.kind}")
        if sse:
            yield gemini_error_sse_event(failure)
        else:
            yield json_array_item(gemini_error_body(failure.status_code, failure.code, failure.message), first=first)
            first = False
    finally:
        with CancelScope(shield=True):
            await gateway.finish(attempt, usage, error=error, cancelled=cancelled)
    if not sse:
        yield JSON_ARRAY_EMPTY if first else JSON_ARRAY_END


async def collect_response(gateway: GeminiGateway, attempt: GeminiAttempt) -> dict[str, Any]:
    usage: GeminiUsage | None = None
    aggregate = GeminiResponseAggregate()
    try:
        async for chunk in _chunks(attempt):
            usage = usage_from_chunk(chunk) or usage
            aggregate.add(chunk)
    except (asyncio.CancelledError, GeneratorExit):
        with CancelScope(shield=True):
            await gateway.finish(attempt, usage, cancelled=True)
        raise
    except Exception as exc:
        error = exc if isinstance(exc, GeminiUpstreamError) else _stream_failure()
        with CancelScope(shield=True):
            await gateway.finish(attempt, usage, error=error)
        raise GeminiGatewayError(error.status_code, error.message, code=f"upstream_{error.kind}") from exc
    with CancelScope(shield=True):
        await gateway.finish(attempt, usage)
    return aggregate.response()
