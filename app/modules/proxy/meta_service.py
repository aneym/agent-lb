from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.clients.failure_observation import observation, observe_status, observe_text
from app.core.routing_table import read_routing_table
from app.core.utils.client_session import reset_caller_seat, set_caller_seat

logger = logging.getLogger(__name__)

UPSTREAM_BASE_URL = os.environ.get("MUSE_UPSTREAM_BASE_URL", "https://api.meta.ai/v1").rstrip("/")
PROVIDER = "muse"


ALLOWED: dict[str, set[str]] = {
    "responses": {"POST"},
    "muse-code/models": {"GET"},
}

_DROP_HEADERS = {
    "host",
    "content-length",
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
_TAIL_BYTES = 128 * 1024

router = APIRouter(prefix="/muse-api", tags=["muse"])
provider_router = APIRouter(prefix="/muse/v1", tags=["muse"])

CREDENTIAL_FILE = Path(
    os.environ.get("MUSE_LB_CREDENTIAL_FILE", str(Path.home() / ".agent-lb/secrets/muse-credential.json"))
).expanduser()
CREDENTIAL_TTL_SECONDS = 300.0
DEFAULT_MODEL = "muse-wonder-1.0"
_CLIENT_AUTH_HEADERS = {"authorization", "x-api-key", "cookie"}


class MuseCredential:
    def __init__(self) -> None:
        self._value: str | None = None
        self._loaded_at = 0.0
        self._lock = asyncio.Lock()

    def _read(self) -> str | None:
        try:
            if CREDENTIAL_FILE.stat().st_mode & 0o077:
                return None
            secret = json.loads(CREDENTIAL_FILE.read_text())
        except (OSError, ValueError):
            return None
        key = secret.get("api_key") if isinstance(secret, dict) else None
        return key if isinstance(key, str) and key else None

    async def get(self, *, refresh: bool = False) -> str | None:
        async with self._lock:
            fresh = time.monotonic() - self._loaded_at < CREDENTIAL_TTL_SECONDS
            if refresh or self._value is None or not fresh:
                self._value = await asyncio.to_thread(self._read)
                self._loaded_at = time.monotonic()
            return self._value


credential = MuseCredential()


def resolve_model(model: str) -> str:
    if model and model != "muse-latest":
        return model
    try:
        alias = read_routing_table("models")["aliases"]["muse-latest"]
        pinned = alias.get("pinned")
        if isinstance(pinned, str) and pinned:
            return pinned
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return DEFAULT_MODEL


def upstream_url(path: str) -> str:
    return f"{UPSTREAM_BASE_URL}/{path}"


def parse_usage_from_sse_tail(tail: bytes) -> tuple[int | None, int | None]:

    input_tokens: int | None = None
    output_tokens: int | None = None
    text = tail.decode("utf-8", "replace")
    stripped = text.strip()
    lines = (
        [stripped] if stripped.startswith("{") else [line[5:] for line in text.splitlines() if line.startswith("data:")]
    )
    for line in lines:
        try:
            event = json.loads(line)
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
    transport: str = "muse-api",
    seat: str | None = None,
) -> None:
    seat_token = set_caller_seat(seat) if seat else None
    try:
        from app.db.session import SessionLocal
        from app.modules.request_logs.repository import RequestLogsRepository

        input_tokens, output_tokens = parse_usage_from_sse_tail(tail)
        if upstream_status is not None:
            observe_status(upstream_status)
        text = tail.decode("utf-8", "replace").strip()
        if text.startswith("{"):
            observe_text(text)
        else:
            for line in text.splitlines():
                if line.startswith("data:"):
                    observe_text(line[5:])
        observed = observation.get()
        error_class = observed.error_class if observed else None
        if error_class:
            status = "error"
        async with SessionLocal() as session:
            await RequestLogsRepository(session).add_log(
                account_id=None,
                request_id=str(uuid.uuid4()),
                model=model or "muse",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                status=status,
                error_code=error_class
                or (f"upstream_{upstream_status}" if status == "error" and upstream_status else None),
                session_id=session_id,
                transport=transport,
                provider=PROVIDER,
                upstream_status_code=upstream_status,
            )
    except Exception:
        logger.exception("muse request log failed")
    finally:
        if seat_token is not None:
            reset_caller_seat(seat_token)


@router.api_route("/{path:path}", methods=["GET", "POST"], response_model=None)
async def forward(request: Request, path: str) -> StreamingResponse | JSONResponse:
    seat = None
    if path.startswith("seat/"):
        seat, _, path = path.removeprefix("seat/").partition("/")
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
    return await _relay(request, path, headers, body, model, "muse-api", seat=seat)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, "type": "agent_lb_muse"}}, status_code=status)


@provider_router.get("/models", response_model=None)
async def provider_models() -> JSONResponse:
    model = resolve_model("muse-latest")
    return JSONResponse(
        {
            "object": "list",
            "data": [
                {"id": "muse-latest", "object": "model", "owned_by": "meta", "resolves_to": model},
                {"id": model, "object": "model", "owned_by": "meta"},
            ],
        }
    )


@provider_router.post("/responses", response_model=None)
async def provider_responses(request: Request) -> StreamingResponse | JSONResponse:
    body = await request.body()
    try:
        payload = json.loads(body) if body else {}
    except ValueError:
        return _error(400, "invalid_json", "request body is not JSON")
    if not isinstance(payload, dict):
        return _error(400, "invalid_json", "request body must be a JSON object")
    model = resolve_model(str(payload.get("model") or ""))
    if model.endswith("-contributor"):
        return _error(
            400,
            "contributor_mode_refused",
            "agent-lb runs Muse with contributor mode off; drop the -contributor suffix",
        )
    if not model.startswith("muse-"):
        return _error(400, "unknown_muse_model", f"{model} is not a Muse model")
    payload["model"] = model
    key = await credential.get()
    if key is None:
        await _log_call(
            model=model,
            session_id=_session_id(request),
            status="error",
            upstream_status=None,
            latency_ms=0,
            tail=b"",
            transport="muse-provider",
        )
        return _error(503, "muse_credential_unavailable", "no Muse login on this agent-lb host")
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in _DROP_HEADERS
        and k.lower() not in _CLIENT_AUTH_HEADERS
        and not k.lower().startswith("x-agent-lb-")
    }
    headers["authorization"] = f"Bearer {key}"

    async def reauth() -> str | None:
        fresh = await credential.get(refresh=True)
        return f"Bearer {fresh}" if fresh is not None and fresh != key else None

    return await _relay(request, "responses", headers, json.dumps(payload).encode(), model, "muse-provider", reauth)


def _session_id(request: Request) -> str | None:
    return request.headers.get("x-tbh-session-id") or request.headers.get("session_id")


async def _relay(
    request: Request,
    path: str,
    headers: dict[str, str],
    body: bytes,
    model: str,
    transport: str,
    reauth: Callable[[], Awaitable[str | None]] | None = None,
    seat: str | None = None,
) -> StreamingResponse | JSONResponse:
    started = time.monotonic()
    client = httpx.AsyncClient(timeout=httpx.Timeout(connect=15.0, read=None, write=60.0, pool=15.0))

    async def send(send_headers: dict[str, str]) -> httpx.Response:
        upstream = client.build_request(
            request.method, upstream_url(path), params=request.query_params, headers=send_headers, content=body
        )
        return await client.send(upstream, stream=True)

    try:
        response = await send(headers)
        if response.status_code == 401 and reauth is not None:
            authorization = await reauth()
            if authorization is not None:
                await response.aclose()
                response = await send({**headers, "authorization": authorization})
    except httpx.HTTPError as exc:
        await client.aclose()
        if path == "responses":
            await _log_call(
                model=model,
                session_id=_session_id(request),
                status="error",
                upstream_status=None,
                latency_ms=int((time.monotonic() - started) * 1000),
                tail=b"",
                transport=transport,
                seat=seat,
            )
        return JSONResponse(
            {"error": {"message": f"muse upstream unreachable: {exc.__class__.__name__}"}},
            status_code=502,
        )

    response_headers = {k: v for k, v in response.headers.items() if k.lower() not in _DROP_HEADERS}

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
                    session_id=_session_id(request),
                    status=status,
                    upstream_status=response.status_code,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    tail=bytes(tail),
                    transport=transport,
                    seat=seat,
                )

    return StreamingResponse(
        stream(),
        status_code=response.status_code,
        headers=response_headers,
        media_type=response.headers.get("content-type"),
    )
