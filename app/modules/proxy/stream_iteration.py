from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from app.modules.proxy.constants import logger


async def prepend_first(first: str | None, stream: AsyncIterator[str]) -> AsyncIterator[str]:
    if first is not None:
        yield first
    async for line in stream:
        yield line


async def read_first_stream_item(stream: AsyncIterator[str]) -> str:
    first = await anext(stream)
    return first


async def prepend_items(items: list[str], stream: AsyncIterator[str]) -> AsyncIterator[str]:
    for item in items:
        yield item
    async for line in stream:
        yield line


async def prepend_first_task(first_task: asyncio.Task[str], stream: AsyncIterator[str]) -> AsyncIterator[str]:
    try:
        yield await first_task
    except StopAsyncIteration:
        return
    async for line in stream:
        yield line


async def prepend_initial_sse_heartbeat(
    stream: AsyncIterator[str],
    keepalive_frame: str,
    *,
    request_id: str | None = None,
    route_family: str = "responses",
) -> AsyncIterator[str]:
    logger.info(
        "responses_stream_heartbeat request_id=%s route_family=%s stage=initial elapsed_seconds=0.000",
        request_id,
        route_family,
    )
    yield keepalive_frame
    async for line in stream:
        yield line
