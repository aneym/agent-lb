"""Outbound upload cap for the proxy's upstream connections ("gaming mode").

agent-lb sends whole model request bodies (hundreds of KB to several MB each)
to Anthropic and OpenAI. On a home uplink of ~43 Mbps those bursts (measured
5.9 MB/s on 2026-09-22) fill the modem's upload queue, and every other device
on the network sees ping spikes (bufferbloat): a game on the same network
stuttered. Pacing the upload below the uplink keeps the queue empty; the
average agent-lb upload (~0.4 MB/s) is far below the cap, so throughput is
barely touched while bursts are flattened.

The cap is a single token bucket shared by every upstream connection. It sits
on the transport of each aiohttp connection to a non-local host (HTTP bodies
and websocket frames alike), so no request path has to opt in. Loopback
connections are never shaped.

State lives in a small JSON file so `agent-lb throttle on|off|status` changes
it without a restart; the running service re-reads it at most once a second:

    {"enabled": true, "bytes_per_sec": 1500000}

With no file the cap is on at AGENT_LB_UPSTREAM_UPLOAD_BYTES_PER_SEC
(default 1.5 MB/s).
"""

from __future__ import annotations

import asyncio
import collections
import contextvars
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_BYTES_PER_SEC = 1_500_000
# Largest single release: small enough that one write cannot itself burst the
# uplink queue, large enough to keep per-write overhead negligible.
BURST_BYTES = 64 * 1024
STATE_REFRESH_SECONDS = 1.0
# Flow control: past HIGH queued bytes the protocol is paused, so aiohttp's
# drain() waits instead of piling a whole upload into memory; it resumes once
# the queue is back under LOW.
PAUSE_HIGH_BYTES = 256 * 1024
PAUSE_LOW_BYTES = 64 * 1024
# Set while aiohttp builds a proxy connection: that leg is upgraded in place
# with start_tls, which needs the loop's own transport.
_IN_PROXY_CONNECTION: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "agent_lb_upload_throttle_in_proxy", default=False
)
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0.0.0.0"})


def state_path() -> Path:
    configured = (os.environ.get("AGENT_LB_THROTTLE_FILE") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".agent-lb" / "state" / "upload-throttle.json"


def default_rate() -> float:
    raw = (os.environ.get("AGENT_LB_UPSTREAM_UPLOAD_BYTES_PER_SEC") or "").strip()
    try:
        value = float(raw) if raw else float(DEFAULT_BYTES_PER_SEC)
    except ValueError:
        value = float(DEFAULT_BYTES_PER_SEC)
    return value if value > 0 else float(DEFAULT_BYTES_PER_SEC)


def read_state() -> tuple[bool, float]:
    """(enabled, bytes_per_sec) from the state file, or the defaults."""
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return True, default_rate()
    if not isinstance(data, dict):
        return True, default_rate()
    enabled = data.get("enabled", True) is not False
    rate = data.get("bytes_per_sec")
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or rate <= 0:
        rate = default_rate()
    return enabled, float(rate)


def write_state(*, enabled: bool, bytes_per_sec: float | None = None) -> dict[str, Any]:
    current_enabled, current_rate = read_state()
    state = {"enabled": enabled, "bytes_per_sec": float(bytes_per_sec or current_rate)}
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state) + "\n", encoding="utf-8")
    os.replace(temp, path)
    return state


class _Bucket:
    """Global token bucket; state is re-read at most once a second."""

    def __init__(self) -> None:
        self.enabled, self.rate = read_state()
        self._tokens = float(BURST_BYTES)
        self._stamp = time.monotonic()
        self._checked = time.monotonic()

    def _refresh(self, now: float) -> None:
        if now - self._checked >= STATE_REFRESH_SECONDS:
            self._checked = now
            self.enabled, self.rate = read_state()

    def take(self, wanted: int) -> tuple[int, float]:
        """Bytes that may go now, and the delay before more are available."""
        now = time.monotonic()
        self._refresh(now)
        if not self.enabled:
            return wanted, 0.0
        self._tokens = min(float(BURST_BYTES), self._tokens + (now - self._stamp) * self.rate)
        self._stamp = now
        allowed = int(min(self._tokens, wanted, BURST_BYTES))
        if allowed <= 0:
            return 0, max(0.001, (min(wanted, BURST_BYTES) - self._tokens) / self.rate)
        self._tokens -= allowed
        return allowed, 0.0


_BUCKET: _Bucket | None = None


def bucket() -> _Bucket:
    global _BUCKET
    if _BUCKET is None:
        _BUCKET = _Bucket()
    return _BUCKET


class ThrottledTransport(asyncio.Transport):
    """Paces writes to the wrapped transport through the shared bucket.

    Writes are queued and released as tokens allow; close() waits for the
    queue to drain, abort() drops it. Everything else is delegated.
    """

    def __init__(self, transport: asyncio.Transport, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self._transport = transport
        self._loop = loop
        self._pending: collections.deque[bytes] = collections.deque()
        self._pending_size = 0
        self._scheduled: asyncio.TimerHandle | asyncio.Handle | None = None
        self._close_after_drain = False
        self._eof_after_drain = False
        self._paused_protocol = False

    # --- writes -----------------------------------------------------------
    def write(self, data: bytes | bytearray | memoryview) -> None:
        if not data:
            return
        if self._transport.is_closing():
            self._transport.write(data)
            return
        chunk = bytes(data)
        if not self._pending:
            sent = self._send_now(chunk)
            if sent == len(chunk):
                return
            chunk = chunk[sent:]
        self._pending.append(chunk)
        self._pending_size += len(chunk)
        self._maybe_pause()
        self._schedule(0.0)

    def _protocol(self) -> Any:
        try:
            return self._transport.get_protocol()
        except Exception:
            return None

    def _maybe_pause(self) -> None:
        if self._paused_protocol or self._pending_size < PAUSE_HIGH_BYTES:
            return
        protocol = self._protocol()
        # Only pause a protocol that is not already paused by the real transport.
        if protocol is None or getattr(protocol, "_paused", False):
            return
        try:
            protocol.pause_writing()
            self._paused_protocol = True
        except Exception:  # pragma: no cover - pacing must never break a connection
            logger.debug("upload_throttle pause_writing failed", exc_info=True)

    def _maybe_resume(self) -> None:
        if not self._paused_protocol or self._pending_size > PAUSE_LOW_BYTES:
            return
        self._paused_protocol = False
        protocol = self._protocol()
        if protocol is None or not getattr(protocol, "_paused", False):
            return
        try:
            protocol.resume_writing()
        except Exception:  # pragma: no cover
            logger.debug("upload_throttle resume_writing failed", exc_info=True)

    def writelines(self, list_of_data: Any) -> None:
        for data in list_of_data:
            self.write(data)

    def _send_now(self, chunk: bytes) -> int:
        sent = 0
        while sent < len(chunk):
            allowed, _ = bucket().take(len(chunk) - sent)
            if allowed <= 0:
                break
            self._transport.write(chunk[sent : sent + allowed])
            sent += allowed
        return sent

    def _schedule(self, delay: float) -> None:
        if self._scheduled is not None:
            return
        if delay <= 0:
            self._scheduled = self._loop.call_soon(self._drain)
        else:
            self._scheduled = self._loop.call_later(delay, self._drain)

    def _drain(self) -> None:
        self._scheduled = None
        try:
            while self._pending:
                if self._transport.is_closing():
                    self._pending.clear()
                    self._pending_size = 0
                    self._paused_protocol = False
                    return
                head = self._pending[0]
                allowed, delay = bucket().take(len(head))
                if allowed <= 0:
                    self._schedule(delay)
                    return
                self._transport.write(head[:allowed])
                self._pending_size -= allowed
                if allowed == len(head):
                    self._pending.popleft()
                else:
                    self._pending[0] = head[allowed:]
                self._maybe_resume()
            if self._eof_after_drain and self._transport.can_write_eof():
                self._transport.write_eof()
            if self._close_after_drain:
                self._transport.close()
        except Exception:  # pragma: no cover - never let pacing kill a connection silently
            logger.exception("upload_throttle drain failed; flushing unpaced")
            while self._pending:
                self._transport.write(self._pending.popleft())
            self._pending_size = 0

    # --- lifecycle --------------------------------------------------------
    def close(self) -> None:
        if self._pending:
            self._close_after_drain = True
            return
        self._transport.close()

    def abort(self) -> None:
        self._pending.clear()
        self._pending_size = 0
        if self._scheduled is not None:
            self._scheduled.cancel()
            self._scheduled = None
        self._transport.abort()

    def write_eof(self) -> None:
        if self._pending:
            self._eof_after_drain = True
            return
        self._transport.write_eof()

    def can_write_eof(self) -> bool:
        return self._transport.can_write_eof()

    def is_closing(self) -> bool:
        return self._close_after_drain or self._transport.is_closing()

    def get_write_buffer_size(self) -> int:
        return self._pending_size + self._transport.get_write_buffer_size()

    def get_write_buffer_limits(self) -> tuple[int, int]:
        return self._transport.get_write_buffer_limits()

    def set_write_buffer_limits(self, high: int | None = None, low: int | None = None) -> None:
        self._transport.set_write_buffer_limits(high, low)

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        return self._transport.get_extra_info(name, default)

    def pause_reading(self) -> None:
        self._transport.pause_reading()

    def resume_reading(self) -> None:
        self._transport.resume_reading()

    def is_reading(self) -> bool:
        return self._transport.is_reading()

    def set_protocol(self, protocol: asyncio.BaseProtocol) -> None:
        self._transport.set_protocol(protocol)

    def get_protocol(self) -> asyncio.BaseProtocol:
        return self._transport.get_protocol()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._transport, name)


_INSTALLED = False


def install() -> None:
    """Wrap every aiohttp connection to a non-local host. Idempotent."""
    global _INSTALLED
    if _INSTALLED:
        return
    from aiohttp import connector as aiohttp_connector

    original = aiohttp_connector.TCPConnector._wrap_create_connection
    original_proxy = aiohttp_connector.TCPConnector._create_proxy_connection

    async def _create_proxy_connection(self: Any, *args: Any, **kwargs: Any) -> Any:
        token = _IN_PROXY_CONNECTION.set(True)
        try:
            return await original_proxy(self, *args, **kwargs)
        finally:
            _IN_PROXY_CONNECTION.reset(token)

    async def _wrap_create_connection(self: Any, *args: Any, req: Any, **kwargs: Any) -> Any:
        transport, protocol = await original(self, *args, req=req, **kwargs)
        if _IN_PROXY_CONNECTION.get():
            return transport, protocol
        host = str(getattr(req, "host", "") or "").strip("[]").lower()
        # Loopback is never shaped, and a CONNECT to an upstream proxy is left raw
        # because start_tls upgrades that transport in place.
        if host in _LOCAL_HOSTS or str(getattr(req, "method", "")).upper() == "CONNECT":
            return transport, protocol
        throttled = ThrottledTransport(transport, asyncio.get_running_loop())
        # aiohttp's writers reach the socket through protocol.transport.
        protocol.transport = throttled
        return throttled, protocol

    aiohttp_connector.TCPConnector._wrap_create_connection = _wrap_create_connection  # type: ignore[method-assign]
    aiohttp_connector.TCPConnector._create_proxy_connection = _create_proxy_connection  # type: ignore[method-assign]
    _INSTALLED = True
    enabled, rate = read_state()
    logger.info("upload_throttle installed enabled=%s bytes_per_sec=%.0f state=%s", enabled, rate, state_path())
