from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.anthropic.models import AnthropicMessageRequest
from app.core.auth.dependencies import validate_proxy_api_key
from app.core.utils.sse import format_sse_event, parse_sse_data_json
from app.modules.proxy.claude_codex_bridge import (
    anthropic_error_from_response,
    claude_to_responses,
    collect_claude_message,
    estimate_claude_input_tokens,
    format_claude_events,
    responses_to_claude_events,
)
from app.modules.proxy.meta_service import provider_responses, resolve_model

router = APIRouter(prefix="/muse/claude/v1", tags=["muse"], dependencies=[Depends(validate_proxy_api_key)])
MODEL = "muse-wonder-full-1.0"


@router.get("/models")
async def models() -> dict:
    return {
        "data": [
            {
                "id": MODEL,
                "type": "model",
                "display_name": resolve_model("muse-latest"),
                "created_at": "2026-10-26T00:00:00Z",
            }
        ],
        "has_more": False,
        "first_id": MODEL,
        "last_id": MODEL,
    }


@router.post("/messages", response_model=None)
async def messages(request: Request, payload: AnthropicMessageRequest) -> JSONResponse | StreamingResponse:
    if payload.model != MODEL:
        return JSONResponse(
            {"type": "error", "error": {"type": "invalid_request_error", "message": f"This route requires {MODEL}"}},
            status_code=400,
        )
    converted = claude_to_responses(payload, resolve_model("muse-latest")).model_dump(mode="json", exclude_none=True)
    for tool in converted.get("tools", []):
        tool["parameters"] = muse_schema(tool["parameters"])
    converted.pop("service_tier", None)
    converted.pop("include", None)
    converted["max_output_tokens"] = payload.max_tokens
    body = json.dumps(converted).encode()
    scope = dict(request.scope)
    scope["headers"] = [
        (key, value)
        for key, value in scope["headers"]
        if key.lower() not in {b"content-length", b"x-tbh-contributor-mode"}
    ]
    scope["headers"].append((b"x-tbh-contributor-mode", b"off"))
    received = False

    async def receive():
        nonlocal received
        if received:
            return await request.receive()
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    upstream = await provider_responses(Request(scope, receive))
    if not isinstance(upstream, StreamingResponse):
        return JSONResponse(
            anthropic_error_from_response(upstream.status_code, bytes(upstream.body)), status_code=upstream.status_code
        )
    if upstream.status_code >= 400:
        chunks = [chunk async for chunk in upstream.body_iterator]
        raw = b"".join(chunk.encode() if isinstance(chunk, str) else bytes(chunk) for chunk in chunks)
        return JSONResponse(anthropic_error_from_response(upstream.status_code, raw), status_code=upstream.status_code)

    async def source() -> AsyncIterator[bytes | str]:
        async for chunk in upstream.body_iterator:
            yield bytes(chunk) if isinstance(chunk, memoryview) else chunk

    events = responses_to_claude_events(
        ordered_muse_events(source()),
        model=MODEL,
        input_tokens_estimate=estimate_claude_input_tokens(payload.model_dump(mode="json", exclude_none=True)),
    )
    stream = format_claude_events(events)
    if not payload.stream:
        return JSONResponse(await collect_claude_message(stream))
    return StreamingResponse(stream, media_type="text/event-stream")


@router.post("/messages/count_tokens")
async def count_tokens(payload: dict) -> dict[str, int]:
    return {"input_tokens": estimate_claude_input_tokens(payload)}


def muse_schema(value):
    if isinstance(value, list):
        return [muse_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: muse_schema(item)
        for key, item in value.items()
        if not (key == "pattern" and isinstance(item, str) and "\\0" in item)
    }


async def ordered_muse_events(source: AsyncIterator[bytes | str]) -> AsyncIterator[str]:
    buffer = ""
    finished_calls: set[str] = set()
    async for chunk in source:
        buffer += chunk.decode("utf-8", "replace") if isinstance(chunk, bytes) else chunk
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            event = parse_sse_data_json(block)
            if event is None:
                continue
            kind = event.get("type")
            item = event.get("item")
            if kind == "response.function_call_arguments.delta" or kind == "response.function_call_arguments.done":
                continue
            if isinstance(item, dict) and item.get("type") == "function_call":
                if kind == "response.output_item.added":
                    continue
                call_id = item.get("call_id") or item.get("id")
                if kind == "response.output_item.done" and isinstance(call_id, str):
                    if call_id in finished_calls:
                        continue
                    finished_calls.add(call_id)
            yield format_sse_event(event)
