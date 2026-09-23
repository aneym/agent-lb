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
