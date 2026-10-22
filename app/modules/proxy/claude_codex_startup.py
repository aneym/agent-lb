from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from app.core.types import JsonObject

_CONTENT_FRAME_TYPES = frozenset({"content_block_start", "content_block_delta"})


async def _empty_events() -> AsyncIterator[JsonObject]:
    return
    yield  # pragma: no cover - makes this a (never-yielding) async generator


STARTUP_FIRST_FRAME_TIMEOUT_SECONDS = 90.0


STARTUP_CONTENT_GRACE_SECONDS = 10.0


async def split_startup_error(
    events: AsyncIterator[JsonObject],
    *,
    first_frame_timeout_seconds: float = STARTUP_FIRST_FRAME_TIMEOUT_SECONDS,
    content_grace_seconds: float = STARTUP_CONTENT_GRACE_SECONDS,
) -> tuple[JsonObject | None, AsyncIterator[JsonObject]]:

    iterator = events.__aiter__()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + first_frame_timeout_seconds
    buffered: list[JsonObject] = []
    pending: asyncio.Future[JsonObject] | None = None
    exhausted = False
    while True:
        if pending is None:
            pending = asyncio.ensure_future(iterator.__anext__())
        done, _ = await asyncio.wait({pending}, timeout=max(0.0, deadline - loop.time()))
        if not done:
            if buffered:
                break
            pending.cancel()
            await asyncio.wait({pending})
            await _aclose(events)
            return _startup_timeout_error(first_frame_timeout_seconds), _empty_events()
        try:
            event = pending.result()
        except StopAsyncIteration:
            pending = None
            exhausted = True
            break
        pending = None
        if event.get("type") == "error":
            await _aclose(events)
            return event, _empty_events()
        if not buffered:
            deadline = loop.time() + content_grace_seconds
        buffered.append(event)
        if event.get("type") in _CONTENT_FRAME_TYPES:
            break

    async def replay() -> AsyncIterator[JsonObject]:
        try:
            for event in buffered:
                yield event
            if exhausted:
                return
            if pending is not None:
                try:
                    yield await pending
                except StopAsyncIteration:
                    return
            async for event in iterator:
                yield event
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
                await asyncio.wait({pending})
            await _aclose(events)

    return None, replay()


def _startup_timeout_error(timeout_seconds: float) -> JsonObject:
    return {
        "type": "error",
        "error": {
            "type": "api_error",
            "message": f"Upstream produced no response within {timeout_seconds:g}s; retry the request",
        },
    }


async def _aclose(events: AsyncIterator[JsonObject]) -> None:
    aclose = getattr(events, "aclose", None)
    if callable(aclose):
        await aclose()
