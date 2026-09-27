"""A forwarded bridge stream runs every step of the owner's work under the forwarded identity."""

from __future__ import annotations

import asyncio

import pytest

from app.core.identity import UNATTRIBUTED_FORWARD_IDENTITY, RequestIdentity, get_request_identity
from app.modules.proxy._service.http_bridge.streaming import _HTTPBridgeStreamingMixin

ORIGIN = RequestIdentity("member-a", "member", "box-2", "tailnet")
wrap = _HTTPBridgeStreamingMixin._stream_with_forwarded_identity


def _recording_stream(seen: list[object], *, fail_at: int | None = None, items: int = 3):
    async def stream():
        try:
            for index in range(items):
                seen.append(get_request_identity())
                if index == fail_at:
                    raise RuntimeError("upstream broke")
                yield f"chunk-{index}"
        finally:
            seen.append(("closed", get_request_identity()))

    return stream()


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", [ORIGIN, UNATTRIBUTED_FORWARD_IDENTITY])
async def test_each_step_sees_the_identity_and_the_caller_context_is_untouched(identity):
    seen: list[object] = []
    wrapped = wrap(_recording_stream(seen), identity)
    # StreamingResponse may resume the generator from different tasks.
    items = [await asyncio.create_task(wrapped.__anext__()) for _ in range(3)]
    assert get_request_identity() is None
    with pytest.raises(StopAsyncIteration):
        await asyncio.create_task(wrapped.__anext__())
    assert items == ["chunk-0", "chunk-1", "chunk-2"]
    assert seen == [identity, identity, identity, ("closed", identity)]
    assert get_request_identity() is None


@pytest.mark.asyncio
async def test_error_mid_stream_propagates_and_resets_the_context():
    seen: list[object] = []
    wrapped = wrap(_recording_stream(seen, fail_at=1), ORIGIN)
    assert await wrapped.__anext__() == "chunk-0"
    with pytest.raises(RuntimeError, match="upstream broke"):
        await wrapped.__anext__()
    assert seen == [ORIGIN, ORIGIN, ("closed", ORIGIN)]
    assert get_request_identity() is None


@pytest.mark.asyncio
async def test_early_close_closes_the_inner_stream_under_the_identity():
    seen: list[object] = []
    wrapped = wrap(_recording_stream(seen), ORIGIN)
    assert await wrapped.__anext__() == "chunk-0"
    await wrapped.aclose()
    assert seen == [ORIGIN, ("closed", ORIGIN)]
    assert get_request_identity() is None


@pytest.mark.asyncio
async def test_cancelled_consumer_cancels_the_inner_stream_and_resets_the_context():
    started, seen = asyncio.Event(), []

    async def slow():
        try:
            seen.append(get_request_identity())
            started.set()
            await asyncio.sleep(3600)
            yield "never"
        finally:
            seen.append(("closed", get_request_identity()))

    wrapped = wrap(slow(), ORIGIN)
    task = asyncio.create_task(wrapped.__anext__())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await wrapped.aclose()
    assert seen == [ORIGIN, ("closed", ORIGIN)]
    assert get_request_identity() is None
