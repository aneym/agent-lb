from __future__ import annotations

import time

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send


class ArrivalTimingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            scope.setdefault("state", {})["arrived_at"] = time.monotonic()
        await self.app(scope, receive, send)


def add_arrival_timing_middleware(app: FastAPI) -> None:
    app.add_middleware(ArrivalTimingMiddleware)
