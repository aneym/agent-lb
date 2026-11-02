from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import Response


def add_arrival_timing_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def arrival_timing_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.arrived_at = time.monotonic()
        return await call_next(request)
