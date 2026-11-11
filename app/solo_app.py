from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import solo_headroom_sender
from app.solo_env import MARKER_ENV
from app.solo_routes import keep_proxy_only

logger = logging.getLogger(__name__)


def _with_headroom_post(app: FastAPI) -> None:
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(owner: FastAPI) -> AsyncIterator[None]:
        async with inner(owner):
            target = solo_headroom_sender.target_from_env()
            task = asyncio.create_task(solo_headroom_sender.run_forever(target)) if target else None
            logger.info("one-person mode up", extra={"headroom_post": bool(target)})
            try:
                yield
            finally:
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

    app.router.lifespan_context = lifespan


def build() -> FastAPI:
    if os.environ.get(MARKER_ENV) != "1":
        raise SystemExit("app.solo_app is started by `agent-lb solo serve`, which sets up its own data dir")
    from app.main import app

    dropped = keep_proxy_only(app)
    _with_headroom_post(app)
    app.state.solo_mode = True
    app.state.solo_dropped_routes = dropped
    return app
