from __future__ import annotations

import asyncio

import pytest

from app.modules.accounts.read_cache import StaleWhileRevalidate


@pytest.mark.asyncio
async def test_clear_during_load_does_not_recache_the_pre_clear_value():
    """A settings write clears the additional-quotas cache while a warmer or
    cold-read load may be in flight; that load must not put its pre-clear
    value back, or /api/accounts serves the old routingPolicy until the TTL."""
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
