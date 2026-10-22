from __future__ import annotations

import http.client
import http.server
import math
import os
import queue
import re
import socket
import socketserver
import sys
import threading
import time

from claude_lb_api import LbApiHandler  # pyrefly: ignore[missing-import]
from claude_lb_common import (  # pyrefly: ignore[missing-import]
    INTERCEPT_HOSTS,
    SHIM_CLIENT_IDLE_TIMEOUT_SECONDS,
    SHIM_TUNNEL_LOG_MIN_BYTES,
    SHIM_TUNNEL_RATE_MBPS_DEFAULT,
    SHIM_TUNNEL_SLOW_CONNECT_SECONDS,
    TunnelBucket,
    env_float,
    splice,
)


def tunnel_bucket_from_env() -> TunnelBucket | None:
    rate_mbps = env_float("CLAUDE_LB_TUNNEL_RATE_MBPS", SHIM_TUNNEL_RATE_MBPS_DEFAULT)

    if not math.isfinite(rate_mbps) or rate_mbps <= 0:
        return None
    return TunnelBucket(rate_mbps * 125_000.0)


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = SHIM_CLIENT_IDLE_TIMEOUT_SECONDS

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_CONNECT(self) -> None:

        target = re.fullmatch(
            r"([A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?)(?::([0-9]{1,5}))?",
            self.path,
        )
        port = int(target.group(2) or 443) if target else 0

        self.close_connection = True
        if target is None or not 1 <= port <= 65535:
            try:
                self.connection.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            except OSError:
                pass
            log_tunnel_connect("", 0, 0.0, "failed")
            return
        host = target.group(1)
        if host in INTERCEPT_HOSTS:
            self._mitm()
        else:
            self._tunnel(host, port)

    def _mitm(self) -> None:

        try:
            self.connection.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        except OSError:
            return

        try:
            raw = socket.socket(
                self.connection.family,
                self.connection.type,
                fileno=os.dup(self.connection.fileno()),
            )
        except OSError:
            return

        raw.settimeout(SHIM_CLIENT_IDLE_TIMEOUT_SECONDS)
        try:
            tls = self.server.tls_context.wrap_socket(raw, server_side=True)  # pyrefly: ignore[missing-attribute]
        except (OSError, ValueError):
            try:
                raw.close()
            except OSError:
                pass
            return
        try:
            LbApiHandler(tls, ("127.0.0.1", 0), self.server)
        except OSError:
            pass
        finally:
            try:
                tls.close()
            except OSError:
                pass

    def _tunnel(self, host: str, port: int) -> None:
        connecting = time.monotonic()
        try:
            if safe_tunnel_target(host, port) == "<invalid target>":
                raise OSError("invalid CONNECT target")
            upstream = socket.create_connection((host, port), timeout=20)
        except OSError:
            try:
                self.connection.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            except OSError:
                pass
            log_tunnel_connect(host, port, time.monotonic() - connecting, "failed")
            return
        try:
            self.connection.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        except OSError:
            try:
                upstream.close()
            except OSError:
                pass
            return
        connect_elapsed = time.monotonic() - connecting
        started = time.monotonic()
        sent, received = splice(self.connection, upstream, getattr(self.server, "tunnel_bucket", None))
        if connect_elapsed >= SHIM_TUNNEL_SLOW_CONNECT_SECONDS:
            log_tunnel_connect(host, port, connect_elapsed, "slow")
        if sent + received >= SHIM_TUNNEL_LOG_MIN_BYTES:
            duration = max(time.monotonic() - started, 0.001)
            queue_tunnel_log(
                f"cc-proxy: tunnel {safe_tunnel_target(host, port)} relayed "
                f"{received / 1e6:.1f}MB down / {sent / 1e6:.1f}MB up in {duration:.0f}s\n"
            )


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


class ThreadingProxyServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    request_queue_size = 1024

    def handle_error(self, request: object, client_address: object) -> None:

        return
