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
from collections.abc import AsyncIterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any

import aiohttp

# Below this, compression saves too little upload time to be worth the CPU.
UPSTREAM_GZIP_MIN_BYTES = 16 * 1024
_GZIP_LEVEL = 6
_DROPPED_HEADERS = frozenset({"content-encoding", "content-length"})
_GZIP_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="upstream-gzip")


def json_body_bytes(payload: Any) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


async def gzip_body(raw: bytes) -> bytes | None:
    """Gzip ``raw`` on the module's own threads, or return None when it is too small to bother.

    A dedicated pool keeps compression off the event loop without queueing
    behind unrelated ``asyncio.to_thread`` work, so its delay stays near the
    ~10 ms a 650 KB body takes. Callers compress before computing a request's
    remaining deadline, so that time counts against the budget.
    """
    if len(raw) < UPSTREAM_GZIP_MIN_BYTES:
        return None
    return await asyncio.get_running_loop().run_in_executor(_GZIP_EXECUTOR, gzip.compress, raw, _GZIP_LEVEL)


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
    gzipped = await gzip_body(json_body_bytes(json_body))
    if gzipped is None:
        request = session.post(url, json=json_body, headers=headers, timeout=timeout)
    else:
        request = session.post(url, data=gzipped, headers=gzip_headers(headers), timeout=timeout)
    async with request as response:
        yield response
