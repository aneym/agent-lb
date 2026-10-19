from __future__ import annotations

import asyncio
import sys
import time
from collections import OrderedDict
from typing import Any

from app.core.clients.codex import CodexClient, create_codex_session
from app.core.upstream_proxy import ResolvedUpstreamRoute


class _Client(CodexClient):
    def __init__(self, account_id: str | None) -> None:
        super().__init__(create_codex_session())
        self.anonymous = account_id is None
        self.busy = True
        self.used_at = time.monotonic()
        self.response: Any = None
        self.complete = False
        self.pool: CodexPool | None = None

    async def request_with_route_metadata(self, *args: Any, **kwargs: Any) -> Any:
        result = await super().request_with_route_metadata(*args, **kwargs)
        if kwargs.get("stream"):
            self.response = result.response
        else:
            self.complete = True
        return result


class CodexPool:
    def __init__(self, capacity: int = 64, idle_seconds: float = 300) -> None:
        self.capacity = capacity
        self.idle_seconds = idle_seconds
        self.entries: dict[tuple[str | None, ResolvedUpstreamRoute], list[_Client]] = {}
        self.idle: OrderedDict[_Client, tuple[str | None, ResolvedUpstreamRoute]] = OrderedDict()
        self.active: dict[_Client, tuple[str | None, ResolvedUpstreamRoute]] = {}
        self.tasks: set[asyncio.Task[None]] = set()
        self.reaper: asyncio.Task[None] | None = None
        self.closed = False

    def _schedule(self, client: _Client) -> None:
        task = asyncio.create_task(client.close())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def _remove(self, client: _Client) -> None:
        key = self.idle.pop(client)
        clients = self.entries[key]
        clients.remove(client)
        if not clients:
            del self.entries[key]

    async def acquire(self, account_id: str | None, route: ResolvedUpstreamRoute) -> CodexClient:
        if self.closed:
            return _Client(account_id)
        if self.reaper is None:
            self.reaper = asyncio.create_task(self._reap())
        self._expire()
        key = (account_id, route)
        clients = self.entries.get(key)
        if clients:
            client = clients[-1]
            self._remove(client)
        else:
            client = _Client(account_id)
            client.pool = self
        client.busy = True
        client.complete = False
        self.active[client] = key
        return client

    async def release(self, client: _Client, error: BaseException | None, complete: bool | None) -> None:
        if not client.busy:
            return
        client.busy = False
        key = self.active.pop(client, None)
        reusable = error is None and (client.complete if complete is None else complete)

        async def finish() -> None:
            healthy = reusable
            try:
                if client.response is not None:
                    response, client.response = client.response, None
                    await response.aclose()
                if client.anonymous:
                    client._session.cookies.clear()
            except BaseException:
                healthy = False
            if healthy and not self.closed and key is not None and len(self.entries.get(key, [])) < 4:
                while self.idle and len(self.active) + len(self.idle) >= self.capacity:
                    oldest = next(iter(self.idle))
                    self._remove(oldest)
                    self._schedule(oldest)
                if len(self.active) + len(self.idle) < self.capacity:
                    client.used_at = time.monotonic()
                    self.entries.setdefault(key, []).append(client)
                    self.idle[client] = key
                    return
            await client.close()

        task = asyncio.create_task(finish())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        await asyncio.shield(task)

    def _expire(self) -> None:
        now = time.monotonic()
        for client in list(self.idle):
            if now - client.used_at < self.idle_seconds:
                break
            self._remove(client)
            self._schedule(client)

    async def _reap(self) -> None:
        while True:
            await asyncio.sleep(min(30, self.idle_seconds))
            self._expire()

    async def close(self) -> None:
        self.closed = True
        if self.reaper is not None:
            self.reaper.cancel()
            await asyncio.gather(self.reaper, return_exceptions=True)
            self.reaper = None
        clients = list(self.idle) + list(self.active)
        self.entries.clear()
        self.idle.clear()
        self.active.clear()
        for client in clients:
            client.busy = False
            self._schedule(client)
        while self.tasks:
            await asyncio.gather(*self.tasks)


_pool: CodexPool | None = None
_closed = False
_unpooled_tasks: set[asyncio.Task[None]] = set()


async def acquire_codex_client(
    account_id: str | None, route: ResolvedUpstreamRoute, *, pooled: bool = True
) -> CodexClient:
    global _pool
    if _closed or not pooled:
        return _Client(account_id)
    if _pool is None:
        _pool = CodexPool()
    return await _pool.acquire(account_id, route)


async def release_codex_client(client: CodexClient | None, complete: bool | None = None) -> None:
    if not isinstance(client, _Client):
        return
    error = sys.exception()
    if client.pool is not None:
        await client.pool.release(client, error, complete)
    elif client.busy:
        client.busy = False
        task = asyncio.create_task(client.close())
        _unpooled_tasks.add(task)
        task.add_done_callback(_unpooled_tasks.discard)
        await asyncio.shield(task)


async def close_codex_pool() -> None:
    global _closed
    _closed = True
    if _pool is not None:
        await _pool.close()
    if _unpooled_tasks:
        await asyncio.gather(*_unpooled_tasks)
