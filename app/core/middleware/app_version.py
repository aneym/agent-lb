from __future__ import annotations

from fastapi import FastAPI
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import __version__


class AppVersionMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_version(message: Message) -> None:
            if message["type"] == "http.response.start":
                status = int(message.get("status", 0))
                if 200 <= status < 500:
                    headers = MutableHeaders(scope=message)
                    headers.setdefault("X-App-Version", __version__)
            await send(message)

        await self.app(scope, receive, send_with_version)


def add_app_version_middleware(app: FastAPI) -> None:
    app.add_middleware(AppVersionMiddleware)
