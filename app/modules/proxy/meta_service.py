

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger(__name__)

UPSTREAM_BASE_URL = os.environ.get("MUSE_UPSTREAM_BASE_URL", "https://api.meta.ai/v1").rstrip("/")
PROVIDER = "muse"



ALLOWED: dict[str, set[str]] = {
    "responses": {"POST"},
    "muse-code/models": {"GET"},
}

_DROP_HEADERS = {
    "host", "content-length", "connection", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade",
}
_TAIL_BYTES = 128 * 1024

router = APIRouter(prefix="/muse-api", tags=["muse"])


def upstream_url(path: str) -> str:
    return f"{UPSTREAM_BASE_URL}/{path}"


def parse_usage_from_sse_tail(tail: bytes) -> tuple[int | None, int | None]:

    input_tokens: int | None = None
    output_tokens: int | None = None
    for line in tail.decode("utf-8", "replace").splitlines():
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:])
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        response = event.get("response")
        usage = response.get("usage") if isinstance(response, dict) else None
        usage = usage or event.get("usage")
        if isinstance(usage, dict):
            if isinstance(usage.get("input_tokens"), int):
                input_tokens = usage["input_tokens"]
            if isinstance(usage.get("output_tokens"), int):
                output_tokens = usage["output_tokens"]
    return input_tokens, output_tokens


async def _log_call(
    *,
    model: str,
    session_id: str | None,
    status: str,
    upstream_status: int | None,
    latency_ms: int,
    tail: bytes,
) -> None:
    try:
        from app.db.session import SessionLocal
        from app.modules.request_logs.repository import RequestLogsRepository

        input_tokens, output_tokens = parse_usage_from_sse_tail(tail)
        async with SessionLocal() as session:
            await RequestLogsRepository(session).add_log(
                account_id=None,
                request_id=str(uuid.uuid4()),
                model=model or "muse",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                status=status,
                error_code=None,
                session_id=session_id,
                transport="muse-api",
                provider=PROVIDER,
                upstream_status_code=upstream_status,
            )
    except Exception:
        logger.exception("muse request log failed")


@router.api_route("/{path:path}", methods=["GET", "POST"], response_model=None)
async def forward(request: Request, path: str) -> StreamingResponse | JSONResponse:
    methods = ALLOWED.get(path)
    if methods is None or request.method not in methods:
        return JSONResponse({"error": {"message": "Not Found"}}, status_code=404)

    body = await request.body()
    model = ""
    if path == "responses" and body:
        try:
            payload = json.loads(body)
            model = str(payload.get("model") or "") if isinstance(payload, dict) else ""
        except ValueError:
            model = ""
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_HEADERS}
    started = time.monotonic()
    client = httpx.AsyncClient(timeout=httpx.Timeout(connect=15.0, read=None, write=60.0, pool=15.0))
    upstream = client.build_request(
        request.method, upstream_url(path), params=request.query_params, headers=headers, content=body
    )
    try:
        response = await client.send(upstream, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        if path == "responses":
            await _log_call(
                model=model,
                session_id=request.headers.get("x-tbh-session-id"),
                status="error",
                upstream_status=None,
                latency_ms=int((time.monotonic() - started) * 1000),
                tail=b"",
            )
        return JSONResponse(
            {"error": {"message": f"muse upstream unreachable: {exc.__class__.__name__}"}},
            status_code=502,
        )

    response_headers = {
        k: v for k, v in response.headers.items() if k.lower() not in _DROP_HEADERS
    }

    async def stream() -> AsyncIterator[bytes]:
        tail = bytearray()
        status = "success" if response.status_code < 400 else "error"
        try:
            async for chunk in response.aiter_raw():
                tail.extend(chunk)
                if len(tail) > _TAIL_BYTES:
                    del tail[: len(tail) - _TAIL_BYTES]
                yield chunk
        except BaseException:
            status = "error"
            raise
        finally:
            await response.aclose()
            await client.aclose()
            if path == "responses":
                await _log_call(
                    model=model,
                    session_id=request.headers.get("x-tbh-session-id"),
                    status=status,
                    upstream_status=response.status_code,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    tail=bytes(tail),
                )

    return StreamingResponse(
        stream(),
        status_code=response.status_code,
        headers=response_headers,
        media_type=response.headers.get("content-type"),
    )
