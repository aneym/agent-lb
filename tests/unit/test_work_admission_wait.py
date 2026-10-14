

from __future__ import annotations

import asyncio
import time

import pytest

from app.core.clients.proxy import ProxyResponseError
from app.modules.proxy.work_admission import WorkAdmissionController

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_admission_waits_before_rejecting() -> None:

    controller = WorkAdmissionController(
        token_refresh_limit=1,
        websocket_connect_limit=0,
        response_create_limit=0,
        compact_response_create_limit=0,
        admission_wait_timeout_seconds=0.5,
    )
    lease = await controller.acquire_token_refresh()

    start = time.monotonic()
    with pytest.raises(ProxyResponseError) as exc_info:
        await controller.acquire_token_refresh()
    elapsed = time.monotonic() - start

    lease.release()
    assert exc_info.value.status_code == 429

    assert elapsed >= 0.3, f"Rejected too fast: {elapsed:.3f}s — should wait ~0.5s"


@pytest.mark.asyncio
async def test_admission_succeeds_when_slot_frees_during_wait() -> None:

    controller = WorkAdmissionController(
        token_refresh_limit=1,
        websocket_connect_limit=0,
        response_create_limit=0,
        compact_response_create_limit=0,
        admission_wait_timeout_seconds=2.0,
    )
    lease = await controller.acquire_token_refresh()

    async def release_after_delay() -> None:
        await asyncio.sleep(0.2)
        lease.release()

    asyncio.create_task(release_after_delay())

    start = time.monotonic()
    second_lease = await controller.acquire_token_refresh()
    elapsed = time.monotonic() - start

    second_lease.release()

    assert elapsed < 1.0, f"Took too long: {elapsed:.3f}s — slot freed at 0.2s"


@pytest.mark.asyncio
async def test_admission_concurrent_burst_queues_not_rejects() -> None:

    controller = WorkAdmissionController(
        token_refresh_limit=2,
        websocket_connect_limit=0,
        response_create_limit=0,
        compact_response_create_limit=0,
        admission_wait_timeout_seconds=5.0,
    )


    lease1 = await controller.acquire_token_refresh()
    lease2 = await controller.acquire_token_refresh()

    results: list[str] = []

    async def try_acquire(name: str, release_after: float) -> None:
        try:
            lease = await controller.acquire_token_refresh()
            results.append(f"{name}:ok")
            await asyncio.sleep(release_after)
            lease.release()
        except ProxyResponseError:
            results.append(f"{name}:rejected")


    async def release_staggered() -> None:
        await asyncio.sleep(0.1)
        lease1.release()
        await asyncio.sleep(0.1)
        lease2.release()

    tasks = [
        asyncio.create_task(try_acquire("a", 0.05)),
        asyncio.create_task(try_acquire("b", 0.05)),
        asyncio.create_task(release_staggered()),
    ]
    await asyncio.gather(*tasks)


    assert results.count("a:ok") + results.count("b:ok") == 2, f"Expected both to succeed, got {results}"


@pytest.mark.asyncio
async def test_response_create_admission_waits() -> None:

    controller = WorkAdmissionController(
        token_refresh_limit=0,
        websocket_connect_limit=0,
        response_create_limit=1,
        compact_response_create_limit=0,
        admission_wait_timeout_seconds=0.5,
    )
    lease = await controller.acquire_response_create()

    async def release_after_delay() -> None:
        await asyncio.sleep(0.15)
        lease.release()

    asyncio.create_task(release_after_delay())

    start = time.monotonic()
    second = await controller.acquire_response_create()
    elapsed = time.monotonic() - start

    second.release()
    assert elapsed < 0.4, f"Took too long: {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_disabled_gate_always_admits() -> None:

    controller = WorkAdmissionController(
        token_refresh_limit=0,
        websocket_connect_limit=0,
        response_create_limit=0,
        compact_response_create_limit=0,
        admission_wait_timeout_seconds=1.0,
    )
    lease = await controller.acquire_response_create()
    lease.release()
