from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from app.core.anthropic.identity import CLAUDE_CODE_ONLY_MESSAGE, is_claude_code_client
from app.modules.model_door.config import DoorConfig
from app.modules.model_door.rails import BoxPass, Rails, RailsUnavailable
from app.modules.model_door.usage import UsageTally

PASS_PREFIX = "rmp_"
DROP_HEADERS = frozenset(
    {
        "authorization",
        "x-api-key",
        "host",
        "content-length",
        "connection",
        "transfer-encoding",
        "cookie",
        "keep-alive",
        "te",
        "upgrade",
        "proxy-authorization",
    }
)
DROP_REPLY = frozenset({"content-length", "transfer-encoding", "connection", "content-encoding", "keep-alive"})


def _error(status: int, kind: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"type": "error", "error": {"type": kind, "message": message}},
        status_code=status,
        headers={"cache-control": "no-store"},
    )


def _given_pass(request: Request) -> str:
    bearer = request.headers.get("authorization", "")
    if bearer[:7].lower() == "bearer ":
        return bearer[7:].strip()
    return request.headers.get("x-api-key", "").strip()


def _client_group(request: Request) -> str | None:
    agent = request.headers.get("user-agent", "").strip()
    return agent.split(maxsplit=1)[0].split("/", 1)[0].strip() or None if agent else None


def _model_of(body: bytes) -> str | None:
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    model = payload.get("model") if isinstance(payload, dict) else None
    return model if isinstance(model, str) else None


class Door:
    def __init__(self, config: DoorConfig) -> None:
        self.config = config
        self.rails = Rails(config.rails, config.relay_key, config.pass_ttl_s)
        self.upstream = httpx.AsyncClient(base_url=config.upstream, timeout=httpx.Timeout(1200.0, connect=10.0))
        config.log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, line: dict[str, Any]) -> None:
        with self.config.log_path.open("a") as out:
            out.write(json.dumps({"t": round(time.time(), 3), **line}) + "\n")

    async def admit(self, request: Request) -> BoxPass | Response:
        given = _given_pass(request)
        if not given.startswith(PASS_PREFIX):
            return _error(
                401,
                "authentication_error",
                "This door takes a Rails model pass for one box (Authorization: Bearer rmp_...).",
            )
        try:
            box_pass = await self.rails.box_pass(given)
        except RailsUnavailable as error:
            self.log({"refused": "rails_unavailable", "why": str(error)})
            return _error(503, "api_error", "Rails could not check the model pass just now. Try again shortly.")
        if box_pass is None:
            return _error(
                401,
                "authentication_error",
                "This model pass is not live: it is unknown, expired or revoked. Ask Rails for a new one.",
            )
        if not is_claude_code_client(_client_group(request)):
            self.log({"refused": "client", "box": box_pass.box, "agent": box_pass.agent})
            return _error(403, "permission_error", CLAUDE_CODE_ONLY_MESSAGE)
        return box_pass

    async def capped(self, box_pass: BoxPass) -> Response | None:
        try:
            check = await self.rails.model_check(box_pass)
        except RailsUnavailable as error:
            self.log({"refused": "rails_unavailable", "why": str(error), "box": box_pass.box})
            return _error(
                503, "api_error", "Rails could not check this account's model cap just now. Try again shortly."
            )
        if check.get("allowed") is True:
            return None
        self.log({"refused": "cap", "box": box_pass.box, "agent": box_pass.agent, "owner": box_pass.owner})
        return _error(
            402,
            "billing_error",
            str(check.get("reason") or check.get("error") or "This account may make no model call now."),
        )

    def upstream_headers(self, request: Request) -> dict[str, str]:
        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in DROP_HEADERS and not k.lower().startswith("x-agent-lb-")
        }
        headers["authorization"] = f"Bearer {self.config.upstream_token}"
        headers["x-agent-lb-seat"] = self.config.seat
        headers["accept-encoding"] = "identity"
        return headers

    async def report(self, box_pass: BoxPass, tally: UsageTally, status: int, began: float, path: str) -> None:
        line: dict[str, Any] = {
            "box": box_pass.box,
            "agent": box_pass.agent,
            "path": path,
            "status": status,
            "s": round(time.monotonic() - began, 2),
            "model": tally.model,
        }
        report = tally.report(f"door-{uuid.uuid4().hex}") if status < 400 else None
        if report:
            line["tokens"] = {
                k: report[k] for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
            }
            try:
                answer = await self.rails.report_usage(box_pass, report)
                line.update(
                    report_id=report["report_id"],
                    cost_usd=answer.get("cost_usd"),
                    over_cap=answer.get("over_cap"),
                    recorded=answer.get("recorded"),
                )
            except RailsUnavailable as error:
                line["report_failed"] = str(error)
        self.log(line)

    async def forward(self, request: Request) -> Response:
        admitted = await self.admit(request)
        if isinstance(admitted, Response):
            return admitted
        refused = await self.capped(admitted)
        if refused:
            return refused
        began = time.monotonic()
        body = await request.body()
        tally = UsageTally(_model_of(body))
        path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        outbound = self.upstream.build_request(
            request.method, path, content=body, headers=self.upstream_headers(request)
        )
        try:
            reply = await self.upstream.send(outbound, stream=True)
        except httpx.HTTPError as error:
            self.log({"refused": "upstream", "why": type(error).__name__, "box": admitted.box})
            return _error(502, "api_error", "The model pool did not answer. Try again shortly.")
        headers = {k: v for k, v in reply.headers.items() if k.lower() not in DROP_REPLY}
        streaming = "text/event-stream" in reply.headers.get("content-type", "")

        async def relay() -> AsyncIterator[bytes]:
            held = b""
            try:
                async for chunk in reply.aiter_bytes():
                    if streaming:
                        tally.feed_stream(chunk)
                    else:
                        held += chunk
                    yield chunk
            finally:
                await reply.aclose()
                if not streaming:
                    tally.feed_body(held)

        task = (
            BackgroundTask(self.report, admitted, tally, reply.status_code, began, request.url.path)
            if request.url.path.endswith("/messages")
            else None
        )
        return StreamingResponse(relay(), status_code=reply.status_code, headers=headers, background=task)

    async def close(self) -> None:
        await self.rails.close()
        await self.upstream.aclose()


async def health(_: Request) -> Response:
    return JSONResponse({"ok": True})


def build_app(config: DoorConfig) -> Starlette:
    door = Door(config)

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        yield
        await door.close()

    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/v1/{rest:path}", door.forward, methods=["GET", "POST"]),
    ]
    return Starlette(routes=routes, lifespan=lifespan)
