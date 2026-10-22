from __future__ import annotations  # noqa: I001


import math
import queue
import re
import select
import socket
import ssl
import sys
import threading
import time

from clients.claude_lb_config import SHIM_TUNNEL_RATE_MBPS_DEFAULT
from clients.claude_lb_config import env_float


class TunnelBucket:
    SMALL_WRITE_BYTES = 8192

    def __init__(self, rate_bytes_per_second: float, burst_bytes: float | None = None) -> None:
        self.rate = rate_bytes_per_second

        capacity = max(rate_bytes_per_second, 131072.0) if burst_bytes is None else burst_bytes
        self.burst_seconds = capacity / rate_bytes_per_second if rate_bytes_per_second > 0 else 0.0
        self.next_free = time.monotonic() - self.burst_seconds
        self.lock = threading.Lock()

    def throttle(self, nbytes: int) -> None:
        if self.rate <= 0:
            return
        with self.lock:
            now = time.monotonic()
            base = max(self.next_free, now - self.burst_seconds)
            wait = base - now
            self.next_free = base + nbytes / self.rate
        if nbytes <= self.SMALL_WRITE_BYTES and wait <= self.burst_seconds:
            return
        while wait > 0:
            step = min(wait, 0.5)
            time.sleep(step)
            wait -= step


def tunnel_bucket_from_env() -> TunnelBucket | None:
    rate_mbps = env_float("CLAUDE_LB_TUNNEL_RATE_MBPS", SHIM_TUNNEL_RATE_MBPS_DEFAULT)

    if not math.isfinite(rate_mbps) or rate_mbps <= 0:
        return None
    return TunnelBucket(rate_mbps * 125_000.0)


def direct_upgrade_tls_context() -> ssl.SSLContext:
    return ssl.create_default_context()


def splice(
    a: socket.socket,
    b: socket.socket,
    bucket: "TunnelBucket | None" = None,
) -> tuple[int, int]:

    ends = (a, b)
    a_to_b = 0
    b_to_a = 0
    try:
        while True:
            readable, _, errored = select.select(ends, [], ends, 300)
            if errored:
                break
            if not readable:
                continue
            for src in readable:
                dst = b if src is a else a
                try:
                    data = src.recv(65536)
                    while getattr(src, "pending", None) is not None and src.pending():
                        data += src.recv(65536)
                except OSError:
                    return a_to_b, b_to_a
                if not data:
                    return a_to_b, b_to_a
                if bucket is not None:
                    bucket.throttle(len(data))
                try:
                    dst.sendall(data)
                except OSError:
                    return a_to_b, b_to_a
                if src is a:
                    a_to_b += len(data)
                else:
                    b_to_a += len(data)
    finally:
        for sock in ends:
            try:
                sock.close()
            except OSError:
                pass
    return a_to_b, b_to_a


_tunnel_logs = queue.Queue(maxsize=32)


_tunnel_log_lock = threading.Lock()


_tunnel_log_worker = None


_tunnel_log_window = 0.0


_tunnel_log_count = 0


_tunnel_log_suppressed = 0


def safe_tunnel_target(host: str, port: int) -> str:
    if (
        re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", host)
        and isinstance(port, int)
        and 1 <= port <= 65535
    ):
        return f"{host}:{port}"
    return "<invalid target>"


def drain_tunnel_logs() -> None:
    while True:
        sink, line = _tunnel_logs.get()
        try:
            sink.write(line)
            sink.flush()
        except Exception:
            pass
        finally:
            _tunnel_logs.task_done()


def queue_tunnel_log(line: str) -> None:

    global _tunnel_log_worker, _tunnel_log_window, _tunnel_log_count, _tunnel_log_suppressed
    if not _tunnel_log_lock.acquire(blocking=False):
        return
    try:
        now = time.monotonic()
        if now - _tunnel_log_window >= 60:
            if _tunnel_log_suppressed:
                try:
                    _tunnel_logs.put_nowait(
                        (sys.stderr, f"cc-proxy: suppressed {_tunnel_log_suppressed} tunnel log lines\n")
                    )
                except queue.Full:
                    pass
            _tunnel_log_window, _tunnel_log_count, _tunnel_log_suppressed = now, 0, 0
        if _tunnel_log_count >= 20:
            _tunnel_log_suppressed += 1
            return
        if _tunnel_log_worker is None:
            _tunnel_log_worker = threading.Thread(target=drain_tunnel_logs, daemon=True)
            _tunnel_log_worker.start()
        try:
            _tunnel_logs.put_nowait((sys.stderr, line))
            _tunnel_log_count += 1
        except queue.Full:
            _tunnel_log_suppressed += 1
    except Exception:
        pass
    finally:
        _tunnel_log_lock.release()


def log_tunnel_connect(host: str, port: int, elapsed: float, outcome: str) -> None:
    try:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        queue_tunnel_log(f"cc-proxy: {stamp} connect {safe_tunnel_target(host, port)} {outcome} after {elapsed:.1f}s\n")
    except Exception:
        pass
