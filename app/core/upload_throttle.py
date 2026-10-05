"""Outbound upload cap for bulk transfers on the proxy's upstream connections.

Model API traffic is never paced (incident 2026-10-05): a game-mode hold of
0.2 MB/s queued Claude Code request bodies until upload admission answered 503
("upload throttle on at 200 KB/s; 3.4 MB queued ahead"). Holds now apply only
to writes made inside ``bulk_transfer()``; every other write, including every
model request body and websocket frame, goes straight to the socket whatever
the holds say. Game mode protects the shared uplink by pausing torrents,
pacing box pushes and capping the gaming PC instead.

Original design notes (2026-09-22), still true for bulk transfers:

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

    {"version": 2, "holds": {"manual": {"bytes_per_sec": 1500000}}, "rate": 1500000}

With no file the cap is on at AGENT_LB_UPSTREAM_UPLOAD_BYTES_PER_SEC
(default 1.5 MB/s).
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import contextvars
import fcntl
import json
import logging
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

DEFAULT_BYTES_PER_SEC = 1_500_000
# Largest single release: small enough that one write cannot itself burst the
# uplink queue, large enough to keep per-write overhead negligible.
BURST_BYTES = 64 * 1024
# Smallest paced release: one full TLS record. Releasing whatever trickled in
# since the previous call (about a byte per microsecond) turned each upload into
# a busy loop of 1-byte writes that held the event loop for the whole upload.
MIN_RELEASE_BYTES = 16 * 1024
STATE_REFRESH_SECONDS = 1.0
# A hold that yields_to another owner is ignored while that owner's "since" is this recent.
ADAPTIVE_FRESH_SECONDS = 90
# Accepted cap range; anything outside it (or not finite) falls back to the default.
MIN_BYTES_PER_SEC = 65_000
MAX_BYTES_PER_SEC = 1_000_000_000
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
# Set only around bulk transfers. Writes outside it (all model API traffic) are
# never paced or queued by a hold.
_BULK: contextvars.ContextVar[bool] = contextvars.ContextVar("agent_lb_upload_bulk", default=False)


@contextlib.contextmanager
def bulk_transfer() -> Iterator[None]:
    """Mark the writes made inside this block (and tasks it starts) as bulk, so holds pace them."""
    token = _BULK.set(True)
    try:
        yield
    finally:
        _BULK.reset(token)


def is_bulk() -> bool:
    return _BULK.get()


def state_path() -> Path:
    configured = (os.environ.get("AGENT_LB_THROTTLE_FILE") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".agent-lb" / "state" / "upload-throttle.json"


def valid_rate(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        # A huge JSON integer cannot become a float; treat it as out of range.
        return math.isfinite(float(value)) and MIN_BYTES_PER_SEC <= value <= MAX_BYTES_PER_SEC
    except (OverflowError, ValueError):
        return False


def default_rate() -> float:
    raw = (os.environ.get("AGENT_LB_UPSTREAM_UPLOAD_BYTES_PER_SEC") or "").strip()
    try:
        value = float(raw) if raw else float(DEFAULT_BYTES_PER_SEC)
    except ValueError:
        value = float(DEFAULT_BYTES_PER_SEC)
    return value if valid_rate(value) else float(DEFAULT_BYTES_PER_SEC)


_FALLBACK_WARNINGS: set[tuple[str, str]] = set()


def _default_policy(rate: float) -> dict[str, Any]:
    return {"holds": {"manual": {"bytes_per_sec": rate, "since": "", "reason": "default protection"}}, "rate": rate}


def _decode_policy(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("state must be an object")
    if data.get("version") != 2:
        if not isinstance(data.get("enabled"), bool) or not valid_rate(data.get("bytes_per_sec")):
            raise ValueError("invalid legacy policy")
        rate = float(data["bytes_per_sec"])
        return {
            "holds": {"manual": {"bytes_per_sec": rate, "since": "", "reason": "legacy hold"}}
            if data["enabled"]
            else {},
            "rate": rate,
        }
    if not valid_rate(data.get("rate")) or not isinstance(data.get("holds"), dict):
        raise ValueError("invalid policy")
    holds = {}
    for owner, hold in data["holds"].items():
        if not isinstance(hold, dict) or not valid_rate(hold.get("bytes_per_sec")):
            _warn_fallback(state_path(), "invalid hold rate for " + owner)
            continue
        entry = {
            "bytes_per_sec": float(hold["bytes_per_sec"]),
            "since": str(hold.get("since", "")),
            "reason": str(hold.get("reason", "")),
        }
        yields_to = hold.get("yields_to")
        if isinstance(yields_to, str) and yields_to.strip():
            entry["yields_to"] = yields_to.strip()
        holds[owner] = entry
    if data["holds"] and not holds:
        raise ValueError("all hold rates invalid")
    return {"holds": holds, "rate": float(data["rate"])}


def _warn_fallback(path: Path, error: str) -> None:
    key = (str(path), error)
    if key not in _FALLBACK_WARNINGS:
        _FALLBACK_WARNINGS.add(key)
        logger.warning("upload throttle state %s: %s", path, error)


def read_policy() -> dict[str, Any]:
    path = state_path()
    data = None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {**_decode_policy(data), "source": "file"}
    except (OSError, ValueError) as error:
        failure = str(error)
    try:
        policy = _decode_policy(json.loads(Path(str(path) + ".last-good.json").read_text(encoding="utf-8")))
        _warn_fallback(path, failure)
        return {**policy, "source": "last_good"}
    except (OSError, ValueError):
        pass
    rate = default_rate()
    if isinstance(data, dict):
        candidates = [data.get("rate"), data.get("bytes_per_sec")]
        if isinstance(data.get("holds"), dict):
            candidates.extend(hold.get("bytes_per_sec") for hold in data["holds"].values() if isinstance(hold, dict))
        rate = min([rate] + [float(value) for value in candidates if valid_rate(value)])
    _warn_fallback(path, failure)
    return {**_default_policy(rate), "source": "default"}


def _parse_since(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _hold_is_fresh(hold: dict[str, Any], now: datetime) -> bool:
    stamp = _parse_since(hold.get("since"))
    if stamp is None:
        return False
    return abs((now - stamp).total_seconds()) <= ADAPTIVE_FRESH_SECONDS


def _hold_yields(holds: dict[str, Any], hold: dict[str, Any], now: datetime) -> bool:
    """True when this hold steps aside for a target owner whose hold is fresh."""
    target = hold.get("yields_to")
    if not isinstance(target, str) or not target:
        return False
    other = holds.get(target)
    return isinstance(other, dict) and _hold_is_fresh(other, now)


def read_state() -> tuple[bool, float]:
    """Effective cap. A hold that yields to a fresh owner is skipped; the minimum of the rest wins."""
    policy = read_policy()
    holds = policy["holds"]
    if not holds:
        return False, policy["rate"]
    now = datetime.now(timezone.utc)
    counted = [hold["bytes_per_sec"] for hold in holds.values() if not _hold_yields(holds, hold, now)]
    return True, min(counted) if counted else policy["rate"]


def _atomic_policy(path: Path, state: dict[str, Any]) -> None:
    temp = Path(str(path) + ".tmp")
    temp.write_text(json.dumps(state) + "\n", encoding="utf-8")
    os.replace(temp, path)


def write_state(
    *,
    enabled: bool,
    bytes_per_sec: float | None = None,
    owner: str = "manual",
    reason: str = "",
    clear_all: bool = False,
    yields_to: str | None = None,
) -> dict[str, Any]:
    if bytes_per_sec is not None and not valid_rate(bytes_per_sec):
        raise ValueError(f"rate must be between {MIN_BYTES_PER_SEC} and {MAX_BYTES_PER_SEC} bytes/s")
    if not owner.strip():
        raise ValueError("owner must not be empty")
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with Path(str(path) + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        policy = read_policy()
        # With no persisted policy, the default protects uploads but is not an
        # operator-owned hold to carry into a detector's first write.
        holds = policy["holds"] if policy["source"] != "default" or path.exists() else {}
        rate = policy["rate"]
        if clear_all:
            holds.clear()
            logger.warning("upload throttle: operator released all holds")
        elif enabled:
            previous = holds.get(owner)
            if not isinstance(previous, dict):
                previous = {}
            hold_rate = bytes_per_sec if bytes_per_sec is not None else previous.get("bytes_per_sec", rate)
            entry: dict[str, Any] = {
                "bytes_per_sec": float(hold_rate),
                "since": datetime.now(timezone.utc).isoformat(),
                "reason": reason,
            }
            kept = previous.get("yields_to") if yields_to is None else yields_to
            if isinstance(kept, str) and kept.strip():
                entry["yields_to"] = kept.strip()
            holds[owner] = entry
            if owner == "manual" and bytes_per_sec is not None:
                rate = float(bytes_per_sec)
        else:
            holds.pop(owner, None)
        state = {"version": 2, "holds": holds, "rate": rate}
        _atomic_policy(path, state)
        _atomic_policy(Path(str(path) + ".last-good.json"), state)
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
        quantum = min(wanted, BURST_BYTES, MIN_RELEASE_BYTES)
        if self._tokens < quantum:
            return 0, max(0.001, (quantum - self._tokens) / self.rate)
        allowed = int(min(self._tokens, wanted, BURST_BYTES))
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
        try:
            self._install_flow_control()
        except Exception:  # pragma: no cover
            logger.debug("upload_throttle could not hook flow control", exc_info=True)

    # --- writes -----------------------------------------------------------
    def write(self, data: bytes | bytearray | memoryview) -> None:
        if not data:
            return
        if self._transport.is_closing():
            self._transport.write(data)
            return
        if not self._pending and not is_bulk():
            # Model API traffic: never paced by a hold. Anything still queued
            # from a bulk write on this connection keeps its place, so byte
            # order holds.
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

    def _install_flow_control(self) -> None:
        """Merge the real transport's pause state with the queue's.

        uvloop pauses the aiohttp protocol when its socket buffer fills, and
        the queue pauses it when too much is waiting here. Both go through
        instance-level hooks so the protocol is paused while either wants it
        and resumed only when neither does; it is never paused twice.
        """
        protocol = self._transport.get_protocol()
        pause = getattr(protocol, "pause_writing", None)
        resume = getattr(protocol, "resume_writing", None)
        if pause is None or resume is None:
            return
        self._flow_protocol = protocol
        self._protocol_pause = pause
        self._protocol_resume = resume
        self._real_paused = False
        self._queue_paused = False
        self._applied_pause = False

        def real_pause() -> None:
            self._real_paused = True
            self._apply_flow()

        def real_resume() -> None:
            self._real_paused = False
            self._apply_flow()

        protocol.pause_writing = real_pause
        protocol.resume_writing = real_resume

    def _apply_flow(self) -> None:
        if not hasattr(self, "_flow_protocol"):
            return
        wanted = self._real_paused or self._queue_paused
        try:
            if wanted and not self._applied_pause:
                self._applied_pause = True
                self._protocol_pause()
            elif not wanted and self._applied_pause:
                self._applied_pause = False
                self._protocol_resume()
        except Exception:  # pragma: no cover - pacing must never break a connection
            logger.debug("upload_throttle flow control failed", exc_info=True)

    def _maybe_pause(self) -> None:
        if hasattr(self, "_flow_protocol") and not self._queue_paused and self._pending_size >= PAUSE_HIGH_BYTES:
            self._queue_paused = True
            self._apply_flow()

    def _maybe_resume(self) -> None:
        if hasattr(self, "_flow_protocol") and self._queue_paused and self._pending_size <= PAUSE_LOW_BYTES:
            self._queue_paused = False
            self._apply_flow()

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
        # Wake at least once per state refresh, so turning the cap off (or up)
        # applies to an upload already waiting.
        delay = min(delay, STATE_REFRESH_SECONDS)
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
                    self._maybe_resume()
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
        self._maybe_resume()
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

    async def _create_proxy_connection(self: Any, req: Any, *args: Any, **kwargs: Any) -> Any:
        token = _IN_PROXY_CONNECTION.set(True)
        try:
            transport, protocol = await original_proxy(self, req, *args, **kwargs)
        finally:
            _IN_PROXY_CONNECTION.reset(token)
        # The leg to the proxy stays raw through start_tls; the finished connection
        # (plain or TLS inside the tunnel) is paced like a direct one.
        host = str(getattr(req, "host", "") or "").strip("[]").lower()
        if host in _LOCAL_HOSTS:
            return transport, protocol
        throttled = ThrottledTransport(transport, asyncio.get_running_loop())
        protocol.transport = throttled
        return throttled, protocol

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
