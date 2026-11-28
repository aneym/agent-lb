from __future__ import annotations

import time
from uuid import uuid4

from fastapi import FastAPI
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.utils.request_id import reset_request_id, set_request_id


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        scope.setdefault("state", {})["request_id_at"] = time.monotonic()
        headers = Headers(scope=scope)
        inbound_request_id = headers.get("x-request-id") or headers.get("request-id")
        request_id = inbound_request_id or str(uuid4())
        token = set_request_id(request_id)

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers.setdefault("x-request-id", request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            reset_request_id(token)


def add_request_id_middleware(app: FastAPI) -> None:
    app.add_middleware(RequestIdMiddleware)
