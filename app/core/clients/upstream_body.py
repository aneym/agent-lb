

from __future__ import annotations

import asyncio
import gzip
import json
import time
from collections.abc import AsyncIterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any

import aiohttp

from app.core.config.settings import get_settings

UPSTREAM_GZIP_MIN_BYTES = 16 * 1024
_GZIP_LEVEL = 6
_DROPPED_HEADERS = frozenset({"content-encoding", "content-length"})
_GZIP_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="upstream-gzip")


def json_body_bytes(payload: Any) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


class CompressionDeadlineExceeded(Exception):
    pass


async def gzip_body(raw: bytes, *, deadline: float | None) -> bytes | None:

    if not get_settings().upstream_request_gzip_enabled or len(raw) < UPSTREAM_GZIP_MIN_BYTES:
        return None
    future = asyncio.get_running_loop().run_in_executor(_GZIP_EXECUTOR, gzip.compress, raw, _GZIP_LEVEL)
    if deadline is None:
        return await future
    try:
        compressed = await asyncio.wait_for(future, timeout=max(0.0, deadline - time.monotonic()))
    except TimeoutError as exc:
        raise CompressionDeadlineExceeded from exc
    if time.monotonic() >= deadline:
        raise CompressionDeadlineExceeded
    return compressed


def gzip_headers(headers: Mapping[str, str]) -> dict[str, str]:
    out = {key: value for key, value in headers.items() if key.lower() not in _DROPPED_HEADERS}
    out["Content-Encoding"] = "gzip"
    return out


@asynccontextmanager
async def post_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    json_body: Any,
    headers: Mapping[str, str],
    timeout: aiohttp.ClientTimeout,
) -> AsyncIterator[aiohttp.ClientResponse]:


    deadline = time.monotonic() + timeout.total if timeout.total is not None else None
    gzipped = await gzip_body(json_body_bytes(json_body), deadline=deadline)
    if gzipped is None:
        request = session.post(url, json=json_body, headers=headers, timeout=timeout)
    else:
        request = session.post(url, data=gzipped, headers=gzip_headers(headers), timeout=timeout)
    async with request as response:
        yield response
