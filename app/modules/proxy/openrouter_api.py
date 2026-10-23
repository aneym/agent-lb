from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator

from anyio import CancelScope
from fastapi import Request, Response
from fastapi.responses import StreamingResponse

from app.core.anthropic.models import AnthropicMessageRequest
from app.dependencies import get_anthropic_proxy_context
from app.modules.api_keys.service_data import ApiKeyData
from app.modules.proxy.anthropic_service import AnthropicProxyError


async def _prime_stream(request: Request, body: AsyncIterator[bytes]) -> bytes | None:
    async def wait_for_disconnect() -> None:
        while (await request.receive())["type"] != "http.disconnect":
            pass

    first = asyncio.ensure_future(anext(body))
    disconnected = asyncio.create_task(wait_for_disconnect())
    primed = False
    try:
        await asyncio.wait((first, disconnected), return_when=asyncio.FIRST_COMPLETED)
        if disconnected.done():
            disconnected.result()
            return None
        chunk = first.result()
        primed = True
        return chunk
    finally:
        first.cancel()
        disconnected.cancel()
        with CancelScope(shield=True):
            await asyncio.gather(first, disconnected, return_exceptions=True)
            if not primed and isinstance(body, AsyncGenerator):
                await body.aclose()


async def chat_response(
    request: Request, effective_model: str, stream_requested: bool | None, api_key: ApiKeyData | None
) -> Response:
    from app.modules.proxy.api import (
        _collect_anthropic_body,
        _enforce_request_limits,
        _estimate_anthropic_request_usage,
        _logged_error_json_response,
        _release_reservation,
        openai_error,
        validate_model_access,
    )

    validate_model_access(api_key, effective_model)
    raw_body = await request.json()
    raw_body["model"] = effective_model
    if stream_requested:
        raw_body["stream_options"] = {**(raw_body.get("stream_options") or {}), "include_usage": True}
    routing_payload = AnthropicMessageRequest(
        model=effective_model,
        max_tokens=raw_body.get("max_completion_tokens") or raw_body.get("max_tokens") or 4096,
        messages=[{"role": "user", "content": ""}],
        stream=stream_requested,
    )
    reservation = await _enforce_request_limits(
        api_key,
        request_model=effective_model,
        request_service_tier=None,
        request_usage_budget=_estimate_anthropic_request_usage(routing_payload, raw_body=await request.body()),
    )

    async def release_body(body: AsyncIterator[bytes], first: bytes) -> AsyncIterator[bytes]:
        try:
            yield first
            async for chunk in body:
                yield chunk
        finally:
            with CancelScope(shield=True):
                await _release_reservation(reservation)

    try:
        stream = await get_anthropic_proxy_context(request).service.stream_messages(
            routing_payload,
            request.headers,
            openai_body=raw_body,
            api_key=api_key,
            api_key_reservation=reservation,
        )
        if stream_requested:
            try:
                first = await _prime_stream(request, stream.body)
                if first is None:
                    await _release_reservation(reservation)
                    return Response(status_code=499)
            except StopAsyncIteration:
                await _release_reservation(reservation)
                return Response(content=b"", media_type=stream.media_type)
            return StreamingResponse(release_body(stream.body, first), media_type=stream.media_type)
        try:
            return Response(content=await _collect_anthropic_body(stream.body), media_type=stream.media_type)
        finally:
            await _release_reservation(reservation)
    except AnthropicProxyError as exc:
        await _release_reservation(reservation)
        return _logged_error_json_response(
            request,
            exc.status_code,
            openai_error(
                exc.code,
                exc.message,
                error_type={401: "authentication_error", 403: "permission_error", 429: "rate_limit_error"}.get(
                    exc.status_code, "invalid_request_error" if exc.status_code < 500 else "server_error"
                ),
            ),
        )
    except BaseException:
        with CancelScope(shield=True):
            await _release_reservation(reservation)
        raise
