from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

from app.core import upload_throttle

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "throttle.json"
    monkeypatch.setenv("AGENT_LB_THROTTLE_FILE", str(path))
    monkeypatch.setattr(upload_throttle, "_BUCKET", None)
    return path


def test_state_defaults_to_on_and_round_trips(_state: Path) -> None:
    assert upload_throttle.read_state() == (True, float(upload_throttle.DEFAULT_BYTES_PER_SEC))
    upload_throttle.write_state(enabled=False)
    assert upload_throttle.read_state()[0] is False
    upload_throttle.write_state(enabled=True, bytes_per_sec=2_000_000)
    assert upload_throttle.read_state() == (True, 2_000_000.0)
    assert json.loads(_state.read_text())["bytes_per_sec"] == 2_000_000.0


class _FakeTransport(asyncio.Transport):
    def __init__(self) -> None:
        super().__init__()
        self.writes: list[tuple[float, int]] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.writes.append((time.monotonic(), len(data)))

    def is_closing(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True

    def abort(self) -> None:
        self.closed = True

    def get_write_buffer_size(self) -> int:
        return 0


@pytest.mark.asyncio
async def test_writes_are_paced_to_the_configured_rate() -> None:
    upload_throttle.write_state(enabled=True, bytes_per_sec=1_000_000)
    fake = _FakeTransport()
    transport = upload_throttle.ThrottledTransport(fake, asyncio.get_running_loop())
    started = time.monotonic()
    transport.write(b"x" * 400_000)
    transport.close()
    while not fake.closed:
        await asyncio.sleep(0.01)
    elapsed = time.monotonic() - started
    assert sum(size for _, size in fake.writes) == 400_000
    assert max(size for _, size in fake.writes) <= upload_throttle.BURST_BYTES
    # 400 KB at 1 MB/s with a 64 KB bucket: at least ~0.33 s, close is deferred until drained.
    assert elapsed >= 0.3


@pytest.mark.asyncio
async def test_off_writes_straight_through() -> None:
    upload_throttle.write_state(enabled=False)
    fake = _FakeTransport()
    transport = upload_throttle.ThrottledTransport(fake, asyncio.get_running_loop())
    transport.write(b"x" * 400_000)
    assert [size for _, size in fake.writes] == [400_000]


@pytest.mark.asyncio
async def test_installed_client_upload_is_paced_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    upload_throttle.write_state(enabled=True, bytes_per_sec=500_000)
    monkeypatch.setattr(upload_throttle, "_LOCAL_HOSTS", frozenset())
    upload_throttle.install()

    received: list[int] = []

    async def handler(request: web.Request) -> web.Response:
        body = await request.read()
        received.append(len(body))
        return web.Response(text="ok")

    app = web.Application(client_max_size=10_000_000)
    app.router.add_post("/", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    try:
        async with aiohttp.ClientSession() as session:
            started = time.monotonic()
            async with session.post(f"http://127.0.0.1:{port}/", data=b"y" * 400_000) as response:
                assert await response.text() == "ok"
            elapsed = time.monotonic() - started
    finally:
        await runner.cleanup()
    assert received == [400_000]
    # 400 KB at 0.5 MB/s: well over half a second when paced (unpaced loopback is ~ms).
    assert elapsed >= 0.6


class _FakeProtocol:
    def __init__(self) -> None:
        self._paused = False
        self.events: list[str] = []

    def pause_writing(self) -> None:
        assert not self._paused
        self._paused = True
        self.events.append("pause")

    def resume_writing(self) -> None:
        assert self._paused
        self._paused = False
        self.events.append("resume")


@pytest.mark.asyncio
async def test_a_large_queue_pauses_the_protocol_until_it_drains() -> None:
    upload_throttle.write_state(enabled=True, bytes_per_sec=2_000_000)
    fake = _FakeTransport()
    protocol = _FakeProtocol()
    fake.get_protocol = lambda: protocol  # type: ignore[method-assign]
    transport = upload_throttle.ThrottledTransport(fake, asyncio.get_running_loop())
    transport.write(b"x" * 600_000)
    assert protocol.events == ["pause"]
    transport.close()
    while not fake.closed:
        await asyncio.sleep(0.01)
    assert protocol.events == ["pause", "resume"]


@pytest.mark.asyncio
async def test_connections_built_for_an_upstream_proxy_are_left_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    upload_throttle.write_state(enabled=True, bytes_per_sec=200_000)
    monkeypatch.setattr(upload_throttle, "_LOCAL_HOSTS", frozenset())
    upload_throttle.install()

    async def handler(request: web.Request) -> web.Response:
        await request.read()
        return web.Response(text="ok")

    app = web.Application(client_max_size=10_000_000)
    app.router.add_post("/", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    token = upload_throttle._IN_PROXY_CONNECTION.set(True)
    try:
        async with aiohttp.ClientSession() as session:
            started = time.monotonic()
            async with session.post(f"http://127.0.0.1:{port}/", data=b"y" * 400_000) as response:
                assert await response.text() == "ok"
            elapsed = time.monotonic() - started
    finally:
        upload_throttle._IN_PROXY_CONNECTION.reset(token)
        await runner.cleanup()
    # Paced at 0.2 MB/s this would take 2 s; the proxy leg is not wrapped.
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_real_and_queue_pauses_merge_into_one_pause_and_one_resume() -> None:
    upload_throttle.write_state(enabled=True, bytes_per_sec=2_000_000)
    fake = _FakeTransport()
    protocol = _FakeProtocol()
    fake.get_protocol = lambda: protocol  # type: ignore[method-assign]
    transport = upload_throttle.ThrottledTransport(fake, asyncio.get_running_loop())
    protocol.pause_writing()  # the real transport's socket buffer fills
    transport.write(b"x" * 600_000)  # the queue also wants a pause
    assert protocol.events == ["pause"]
    while transport.get_write_buffer_size() > upload_throttle.PAUSE_LOW_BYTES:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    assert protocol.events == ["pause"]  # the socket is still full: no resume yet
    protocol.resume_writing()  # the real transport drains
    assert protocol.events == ["pause", "resume"]
    transport.abort()


def test_non_finite_or_out_of_range_rates_fall_back_to_the_default(_state: Path) -> None:
    for bad in ("NaN", "Infinity", "1", "1e308", "1" + "0" * 400):
        _state.write_text('{"enabled": true, "bytes_per_sec": %s}' % bad)
        assert upload_throttle.read_state() == (True, float(upload_throttle.DEFAULT_BYTES_PER_SEC))
    with pytest.raises(ValueError):
        upload_throttle.write_state(enabled=True, bytes_per_sec=float("inf"))


def test_a_waiting_upload_wakes_within_a_state_refresh() -> None:
    loop = asyncio.new_event_loop()
    try:
        transport = upload_throttle.ThrottledTransport(_FakeTransport(), loop)
        transport._schedule(3600.0)
        assert transport._scheduled is not None
        assert transport._scheduled.when() - loop.time() <= upload_throttle.STATE_REFRESH_SECONDS + 0.01
        transport._scheduled.cancel()
    finally:
        loop.close()



def test_the_documented_minimum_rate_is_accepted() -> None:
    assert upload_throttle.valid_rate(0.065 * 1_000_000)
    assert not upload_throttle.valid_rate(0.064 * 1_000_000)
