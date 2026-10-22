from __future__ import annotations
import hashlib
import json
import os
import time
import uuid
from lb_sandbox_filesystem import (
    read_root_json,
)
from lb_sandbox_paths import (
    HOLD_MAX_S,
    now_iso,
)


def rate_limit_response(edge: str) -> tuple[int, dict[str, str], bytes]:
    reset = int(time.time()) + 600
    if edge == "anthropic":
        body = {"type": "error", "error": {"type": "rate_limit_error", "message": "lb-sandbox injected account_429"}}
        headers = {
            "retry-after": "600",
            "anthropic-ratelimit-unified-status": "rejected",
            "anthropic-ratelimit-unified-reset": str(reset),
            "request-id": f"req_lbsandbox_{uuid.uuid4().hex[:16]}",
        }
    else:
        body = {
            "error": {
                "type": "rate_limit_exceeded",
                "code": "rate_limit_exceeded",
                "message": "lb-sandbox injected account_429",
                "resets_in_seconds": 600,
                "resets_at": reset,
            }
        }
        headers = {"retry-after": "600"}
    headers["content-type"] = "application/json"
    return 429, headers, json.dumps(body).encode()


HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)


def auth_hash12(headers) -> str:
    value = headers.get("Authorization") or headers.get("x-api-key") or ""
    return hashlib.sha256(value.encode()).hexdigest()[:12] if value else ""


class FaultState:


    def __init__(self, root: Path, rel: str) -> None:
        self.root, self.rel = root, rel
        self.path = root / rel
        self.seen = None
        self.nonce = None
        self.fault: str | None = None
        self.n: int | None = None
        self.failed: set[str] = set()
        self.consumed = False

    def refresh(self) -> None:


        try:
            info = os.stat(self.path, follow_symlinks=False)
            seen = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)
        except OSError:
            seen = None
        if seen == self.seen:
            return
        self.seen = seen
        data = read_root_json(self.root, self.rel) if seen is not None else {}
        if data.get("nonce") != self.nonce:
            self.nonce = data.get("nonce")
            self.fault = data.get("fault") if data.get("armed") else None
            self.n = data.get("n")
            self.failed = set()
            self.consumed = False

    def take_429(self, auth_hash: str) -> bool:

        if self.fault != "account_429":
            return False
        if not self.failed:
            self.failed.add(auth_hash)
            return True
        return auth_hash in self.failed

    def take_cut(self) -> int | None:
        if self.fault != "cut_after_bytes" or self.consumed or not isinstance(self.n, int):
            return None
        self.consumed = True
        return self.n

    def take_hold(self) -> tuple[int, object] | None:

        if self.fault != "hold_stream" or self.consumed or not isinstance(self.n, int):
            return None
        self.consumed = True
        return min(self.n, HOLD_MAX_S), self.nonce

    def released(self, nonce: object) -> bool:

        self.refresh()
        return self.nonce != nonce


MAX_DECODED_BODY = 64 << 20


STREAMED_PATHS = ("/codex/responses",)


def request_json(body: bytes, headers) -> dict:

    encoding = (headers.get("Content-Encoding") or "").strip().lower()
    if encoding == "gzip" and body[:2] == b"\x1f\x8b":
        import zlib

        try:
            inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
            body = inflater.decompress(body, MAX_DECODED_BODY)
            if inflater.unconsumed_tail:
                return {}
        except zlib.error:
            return {}
    elif encoding not in ("", "identity", "gzip"):
        return {}
    try:
        parsed = json.loads(body or b"{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def wants_stream(method: str, path: str, parsed: dict) -> bool:

    return method == "POST" and (parsed.get("stream") is True or path.endswith(STREAMED_PATHS))


def sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


async def serve_held_stream(request, fault: FaultState, bound: int, nonce, model: str) -> tuple[object, dict]:

    import asyncio

    from aiohttp import web

    response = web.StreamResponse(
        status=200,
        headers={
            "content-type": "text/event-stream",
            "cache-control": "no-cache",
            "request-id": f"req_lbsandbox_{uuid.uuid4().hex[:16]}",
        },
    )
    await response.prepare(request)
    message = {
        "id": f"msg_lbsandbox_{uuid.uuid4().hex[:16]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    sent = 0
    for chunk in (
        sse("message_start", {"type": "message_start", "message": message}),
        sse(
            "content_block_start",
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        ),
        sse(
            "content_block_delta",
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "1, 2, 3"}},
        ),
    ):
        await response.write(chunk)
        sent += len(chunk)
    started = time.monotonic()
    released_by, last_ping = "timeout", started
    while time.monotonic() - started < bound:
        if fault.released(nonce):
            released_by = "clear"
            break
        if time.monotonic() - last_ping >= 2:
            ping = sse("ping", {"type": "ping"})
            await response.write(ping)
            sent += len(ping)
            last_ping = time.monotonic()
        await asyncio.sleep(0.1)
    held = round(time.monotonic() - started, 1)
    for chunk in (
        sse(
            "content_block_delta",
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": ", 4, 5"}},
        ),
        sse("content_block_stop", {"type": "content_block_stop", "index": 0}),
        sse(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 5},
            },
        ),
        sse("message_stop", {"type": "message_stop"}),
    ):
        await response.write(chunk)
        sent += len(chunk)
    await response.write_eof()
    return response, {"status": 200, "bytes": sent, "released_by": released_by, "held_s": held}


def make_edge_handler(edge: str, base: str, fault: FaultState, session, counters: dict, log):

    import asyncio

    import aiohttp
    from aiohttp import web

    async def handle(request: web.Request) -> web.StreamResponse:
        started = time.monotonic()
        body = await request.read()
        auth = auth_hash12(request.headers)
        counters["edge_requests"][edge] += 1
        fault.refresh()
        record = {"ts": now_iso(), "edge": edge, "method": request.method, "path": request.path}
        record["auth_hash12"] = auth
        if fault.take_429(auth):
            counters["faults_applied"][edge] += 1
            status, headers, payload = rate_limit_response(edge)
            log({**record, "status": status, "bytes": len(payload), "fault": "account_429"})
            return web.Response(status=status, body=payload, headers=headers)


        armed = fault.fault in ("hold_stream", "cut_after_bytes") and not fault.consumed
        parsed = request_json(body, request.headers) if armed else {}
        streamed = armed and wants_stream(request.method, request.path, parsed)
        armed_hold = fault.fault == "hold_stream" and not fault.consumed
        if armed_hold and edge == "anthropic" and request.method == "POST" and request.path == "/v1/messages":
            hold = fault.take_hold() if parsed.get("stream") is True else None
            if hold is not None:
                counters["faults_applied"][edge] += 1
                try:
                    response, fields = await serve_held_stream(
                        request, fault, hold[0], hold[1], str(parsed.get("model", ""))
                    )
                except (ConnectionResetError, asyncio.CancelledError):
                    log(
                        {
                            **record,
                            "status": 200,
                            "fault": "hold_stream",
                            "downstream_closed": True,
                            "ms": int((time.monotonic() - started) * 1000),
                        }
                    )
                    raise
                log({**record, **fields, "fault": "hold_stream", "ms": int((time.monotonic() - started) * 1000)})
                return response
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
        applied, status, sent, error, downstream_closed = None, 0, 0, None, False
        response: web.StreamResponse | None = None

        def drop_downstream() -> None:


            if request.transport is not None:
                request.transport.close()

        try:
            async with session.request(
                request.method,
                f"{base}{request.path_qs}",
                headers=headers,
                data=body or None,
                allow_redirects=False,
            ) as upstream:
                status = upstream.status
                out_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP}
                response = web.StreamResponse(status=status, headers=out_headers)
                await response.prepare(request)
                cut_at = fault.take_cut() if streamed and status == 200 else None
                if cut_at is not None:
                    applied = f"cut_after_bytes:{cut_at}"
                    counters["faults_applied"][edge] += 1
                try:
                    async for chunk in upstream.content.iter_any():
                        if cut_at is not None and sent + len(chunk) >= cut_at:
                            part = chunk[: max(0, cut_at - sent)]
                            if part:
                                await response.write(part)
                                sent += len(part)
                            drop_downstream()
                            break
                        await response.write(chunk)
                        sent += len(chunk)
                    else:
                        await response.write_eof()
                except ConnectionResetError:

                    downstream_closed = True
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            error = exc.__class__.__name__
            if response is not None and response.prepared:


                drop_downstream()
        ms = int((time.monotonic() - started) * 1000)
        entry = {**record, "status": status, "bytes": sent, "fault": applied, "ms": ms}
        if applied:
            entry["cut"] = True
        if downstream_closed:
            entry["downstream_closed"] = True
        if error:
            entry["error"] = error
        log(entry)
        if response is None:
            return web.json_response({"error": {"type": "lb_sandbox_edge", "message": error}}, status=502)
        return response

    return handle
