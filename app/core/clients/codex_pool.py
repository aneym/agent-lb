from __future__ import annotations

import asyncio
import sys
import time
from collections import OrderedDict
from typing import Any

from curl_cffi import CurlError

from app.core.clients.codex import CodexClient, CodexTransportError, create_codex_session
from app.core.upstream_proxy import ResolvedUpstreamRoute


class _Client(CodexClient):
    def __init__(self, account_id: str | None) -> None:
        super().__init__(create_codex_session())
        self.anonymous = account_id is None
        self.busy = False
        self.broken = False
        self.used_at = time.monotonic()
        self.response: Any = None

    async def request_with_route_metadata(self, *args: Any, **kwargs: Any) -> Any:
        try:
            result = await super().request_with_route_metadata(*args, **kwargs)
            if kwargs.get("stream"):
                self.response = result.response
            return result
        except (CodexTransportError, CurlError):
            self.broken = True
            raise

    async def open_ws_with_route_metadata(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return await super().open_ws_with_route_metadata(*args, **kwargs)
        except (CodexTransportError, CurlError):
            self.broken = True
            raise


class CodexPool:
    def __init__(self, capacity: int = 64, idle_seconds: float = 300) -> None:
        self.capacity = capacity
        self.idle_seconds = idle_seconds
        self.entries: OrderedDict[tuple[str | None, ResolvedUpstreamRoute], _Client] = OrderedDict()
        self.condition = asyncio.Condition()
        self.reaper: asyncio.Task[None] | None = None
        self.closed = False

    async def acquire(self, account_id: str | None, route: ResolvedUpstreamRoute) -> CodexClient:
        key = (account_id, route)
        async with self.condition:
            if self.reaper is None:
                self.reaper = asyncio.create_task(self._reap())
            while not self.closed:
                await self._expire()
                client = self.entries.get(key)
                if client is not None and not client.busy:
                    client.busy = True
                    self.entries.move_to_end(key)
                    return client
                if client is None:
                    if len(self.entries) >= self.capacity:
                        for old_key, old_client in self.entries.items():
                            if not old_client.busy:
                                del self.entries[old_key]
                                await old_client.close()
                                break
                    if len(self.entries) < self.capacity:
                        client = _Client(account_id)
                        client.busy = True
                        self.entries[key] = client
                        return client
                await self.condition.wait()
        raise RuntimeError("Codex pool is closed")

    async def release(self, client: CodexClient, error: BaseException | None) -> None:
        if not isinstance(client, _Client):
            return
        async with self.condition:
            if not client.busy:
                return
            if client.response is not None:
                response, client.response = client.response, None
                try:
                    await response.aclose()
                except (CodexTransportError, CurlError):
                    client.broken = True
            if client.anonymous:
                client._session.cookies.clear()
            client.busy = False
            client.used_at = time.monotonic()
            if client.broken or isinstance(error, (CodexTransportError, CurlError, asyncio.CancelledError)):
                for key, value in list(self.entries.items()):
                    if value is client:
                        del self.entries[key]
                await client.close()
            self.condition.notify_all()

    async def _expire(self) -> None:
        now = time.monotonic()
        for key, client in list(self.entries.items()):
            if not client.busy and now - client.used_at >= self.idle_seconds:
                del self.entries[key]
                await client.close()

    async def _reap(self) -> None:
        while True:
            await asyncio.sleep(min(30, self.idle_seconds))
            async with self.condition:
                await self._expire()
                self.condition.notify_all()

    async def close(self) -> None:
        async with self.condition:
            self.closed = True
            if self.reaper is not None:
                self.reaper.cancel()
                await asyncio.gather(self.reaper, return_exceptions=True)
                self.reaper = None
            clients = list(self.entries.values())
            self.entries.clear()
            self.condition.notify_all()
            await asyncio.gather(*(client.close() for client in clients))


_pool: CodexPool | None = None


async def acquire_codex_client(account_id: str | None, route: ResolvedUpstreamRoute) -> CodexClient:
    global _pool
    if _pool is None:
        _pool = CodexPool()
    return await _pool.acquire(account_id, route)


async def release_codex_client(client: CodexClient | None) -> None:
    if _pool is not None and client is not None:
        await _pool.release(client, sys.exception())


async def close_codex_pool() -> None:
    global _pool
    pool, _pool = _pool, None
    if pool is not None:
        await pool.close()
