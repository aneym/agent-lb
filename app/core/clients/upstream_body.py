"""Gzip large JSON request bodies on their way upstream.

Studio's uplink is the bottleneck for model calls: a 700 KB Messages body
takes seconds to upload, and it gzips about 3.8x. Anthropic's Messages API
and the ChatGPT Codex backend both accept ``Content-Encoding: gzip`` request
bodies (probed 2026-10-05; Anthropic rejects zstd, br and deflate with
``request_body_encoding_unsupported``), so gzip is the one shared encoding.
"""

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

# Below this, compression saves too little upload time to be worth the CPU.
UPSTREAM_GZIP_MIN_BYTES = 16 * 1024
_GZIP_LEVEL = 6
_DROPPED_HEADERS = frozenset({"content-encoding", "content-length"})
_GZIP_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="upstream-gzip")


def json_body_bytes(payload: Any) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


class CompressionDeadlineExceeded(Exception):
    """The request's deadline passed before its body finished compressing; nothing was sent."""


async def gzip_body(raw: bytes, *, deadline: float | None) -> bytes | None:
    """Gzip ``raw`` on the module's own threads, or return None to send it as is.

    None means gzip is off (``upstream_request_gzip_enabled``) or ``raw`` is
    under the threshold. ``deadline`` is a ``time.monotonic()`` instant: when
    it passes before compression finishes, even while waiting for a free
    worker, this raises ``CompressionDeadlineExceeded`` so the caller fails the
    request before upload instead of starting it on an expired budget.
    """
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
    """``session.post(url, json=...)``, with the body gzipped when it is large."""
    # aiohttp's total timer starts at post(); bound compression by the same budget.
    deadline = time.monotonic() + timeout.total if timeout.total is not None else None
    gzipped = await gzip_body(json_body_bytes(json_body), deadline=deadline)
    if gzipped is None:
        request = session.post(url, json=json_body, headers=headers, timeout=timeout)
    else:
        request = session.post(url, data=gzipped, headers=gzip_headers(headers), timeout=timeout)
    async with request as response:
        yield response
