from __future__ import annotations

import asyncio

import pytest

from app.modules.accounts.read_cache import StaleWhileRevalidate


@pytest.mark.asyncio
async def test_clear_during_load_does_not_recache_the_pre_clear_value():

    cache: StaleWhileRevalidate[str] = StaleWhileRevalidate("test", ttl_seconds=60.0)
    load_started = asyncio.Event()
    release_load = asyncio.Event()

    async def slow_old_load() -> str:
        load_started.set()
        await release_load.wait()
        return "old-policy"

    in_flight = asyncio.create_task(cache.refresh_now("key", slow_old_load))
    await load_started.wait()
    cache.clear()
    release_load.set()
    assert await in_flight == "old-policy"

    async def new_load() -> str:
        return "new-policy"

    async def unused_refresh() -> str:
        raise AssertionError("a cleared slot must load, not refresh in the background")

    assert await cache.get("key", load=new_load, refresh=unused_refresh) == "new-policy"
