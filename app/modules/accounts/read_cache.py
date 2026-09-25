from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Hashable
from typing import Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class StaleWhileRevalidate(Generic[T]):
    """One cached value for the accounts read path, refreshed off the request path.

    A read whose key matches gets the cached value however old it is; once it is
    older than ``ttl_seconds`` the read also starts one background refresh (never two
    at a time). Only an empty or re-keyed slot makes the reader wait for a load.
    ``refresh_now`` reloads in the caller, for the startup warm and the warmer loop.
    """

    def __init__(self, name: str, *, ttl_seconds: float) -> None:
        self._name = name
        self._ttl_seconds = ttl_seconds
        self._key: Hashable | None = None
        self._value: T | None = None
        self._loaded_at = 0.0
        self._generation = 0
        self._refresh_task: asyncio.Task[None] | None = None

    async def get(
        self,
        key: Hashable,
        *,
        load: Callable[[], Awaitable[T]],
        refresh: Callable[[], Awaitable[T]],
    ) -> T:
        if self._value is not None and self._key == key:
            if time.monotonic() - self._loaded_at >= self._ttl_seconds:
                self._start_refresh(key, refresh)
            return self._value
        return await self.refresh_now(key, load)

    async def refresh_now(self, key: Hashable, load: Callable[[], Awaitable[T]]) -> T:
        started_at = time.monotonic()
        value = await load()
        self._store(key, value, started_at)
        return value

    async def cancel_refresh(self) -> None:
        """Stop an in-flight background refresh (shutdown, before the DB closes)."""
        task, self._refresh_task = self._refresh_task, None
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    def clear(self) -> None:
        self._key = None
        self._value = None
        self._loaded_at = 0.0
        self._generation += 1
        self._refresh_task = None

    def _store(self, key: Hashable, value: T, started_at: float) -> None:
        self._key = key
        self._value = value
        self._loaded_at = started_at
        self._generation += 1

    def _start_refresh(self, key: Hashable, refresh: Callable[[], Awaitable[T]]) -> None:
        if self._refresh_task is not None and not self._refresh_task.done():
            return
        self._refresh_task = asyncio.create_task(
            self._run_refresh(key, refresh, self._generation),
            name=f"accounts-read-cache:{self._name}",
        )

    async def _run_refresh(self, key: Hashable, refresh: Callable[[], Awaitable[T]], generation: int) -> None:
        started_at = time.monotonic()
        try:
            value = await refresh()
        except Exception:
            logger.warning("accounts %s cache refresh failed; serving the previous value", self._name, exc_info=True)
            return
        # A clear or a newer load since this refresh started wins over its result.
        if self._generation == generation:
            self._store(key, value, started_at)
