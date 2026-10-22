from __future__ import annotations  # noqa: I001


import http.client
import http.server
import json
import os
import re
import socket
import socketserver
import ssl
import threading
import time
import urllib.request
from typing import cast

from clients.claude_lb_activity import LbActivity
from clients.claude_lb_activity import client_activity_path
from clients.claude_lb_command import CcgptModelViolation
from clients.claude_lb_command import ccgpt_upstream_path
from clients.claude_lb_config import AGENT_LB_ANTHROPIC_PATHS
from clients.claude_lb_config import DIRECT_UPGRADE_ADDRESS
from clients.claude_lb_config import DIRECT_UPGRADE_HOST
from clients.claude_lb_config import HOP_BY_HOP_HEADERS
from clients.claude_lb_config import INTERCEPT_HOSTS
from clients.claude_lb_config import SHIM_CLIENT_IDLE_TIMEOUT_SECONDS
from clients.claude_lb_config import SHIM_CONNECT_BACKOFF_CAP_DEFAULT
from clients.claude_lb_config import SHIM_CONNECT_BACKOFF_DEFAULT
from clients.claude_lb_config import SHIM_CONNECT_RETRIES_DEFAULT
from clients.claude_lb_config import SHIM_TUNNEL_LOG_MIN_BYTES
from clients.claude_lb_config import SHIM_TUNNEL_SLOW_CONNECT_SECONDS
from clients.claude_lb_config import env_float
from clients.claude_lb_config import env_int
from clients.claude_lb_config import is_retryable_shim_connect_error
from clients.claude_lb_transport import TunnelBucket
from clients.claude_lb_transport import direct_upgrade_tls_context
from clients.claude_lb_transport import log_tunnel_connect
from clients.claude_lb_transport import queue_tunnel_log
from clients.claude_lb_transport import safe_tunnel_target
from clients.claude_lb_transport import splice


def routes_to_agent_lb(path: str) -> bool:

    parsed = urllib.parse.urlsplit(path)
    if parsed.scheme or parsed.netloc:
        return False
    return parsed.path in AGENT_LB_ANTHROPIC_PATHS or (
        parsed.path.endswith("/") and parsed.path[:-1] in AGENT_LB_ANTHROPIC_PATHS
    )


class LbApiHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = SHIM_CLIENT_IDLE_TIMEOUT_SECONDS

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        self._forward()

    def do_POST(self) -> None:
        self._forward()

    def do_PUT(self) -> None:
        self._forward()

    def do_PATCH(self) -> None:
        self._forward()

    def do_DELETE(self) -> None:
        self._forward()

    def do_HEAD(self) -> None:
        self._forward()

    def _forward(self) -> None:
        parent_pid = cast(ThreadingProxyServer, self.server).parent_pid
        session_id = cast(ThreadingProxyServer, self.server).session_id
        upstream_base_url = cast(ThreadingProxyServer, self.server).upstream_base_url
        shared = cast(ThreadingProxyServer, self.server).shared

        if not shared:
            try:
                assert parent_pid is not None
                os.kill(parent_pid, 0)
            except OSError:
                self.send_error(503, "parent process exited")
                return

        if self._is_websocket_upgrade() and not routes_to_agent_lb(self.path):
            self._tunnel_upgrade()
            return

        body = None
        if self.command not in {"GET", "HEAD"}:
            try:
                length = int(self.headers.get("content-length", "0"))
            except ValueError:
                length = 0
            body = self.rfile.read(length) if length > 0 else b""

        routes_to_lb = routes_to_agent_lb(self.path)
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() != "host"
        }
        if routes_to_lb and session_id:
            headers["x-claude-session-id"] = session_id
            if not shared:
                headers.update(getattr(cast(ThreadingProxyServer, self.server), "tag_headers", {}))

        if not routes_to_lb:
            self._forward_direct(body, headers)
            return

        try:
            upstream_path = (
                ccgpt_upstream_path(self.path, body)
                if cast(ThreadingProxyServer, self.server).ccgpt_mode
                else self.path
            )
        except CcgptModelViolation as error:
            message = json.dumps(
                {
                    "type": "error",
                    "error": {
                        "type": "invalid_request_error",
                        "message": str(error),
                    },
                }
            ).encode()
            self.send_response(400)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(message)))
            self.send_header("connection", "close")
            self.end_headers()
            self.wfile.write(message)
            self.close_connection = True
            return
        request = urllib.request.Request(
            f"{upstream_base_url}{upstream_path}",
            data=body,
            headers=headers,
            method=self.command,
        )
        activity = getattr(cast(ThreadingProxyServer, self.server), "activity", None)
        client_id = None
        client_lock = cast(ThreadingProxyServer, self.server).client_activity_lock if shared else None
        client_activities = cast(ThreadingProxyServer, self.server).client_activity if shared else None
        if shared and activity is None:
            client_id = self.headers.get("x-claude-code-session-id")
            client_path = client_activity_path(client_id) if client_id is not None else None
            if client_path is not None and client_path.exists():
                client_activities = getattr(cast(ThreadingProxyServer, self.server), "client_activity", None)
                client_lock = getattr(cast(ThreadingProxyServer, self.server), "client_activity_lock", None)
                if client_activities is not None and client_lock is not None:
                    with client_lock:
                        activity = client_activities.get(client_id)
                        if activity is None:
                            activity = LbActivity(client_path, only_if_exists=True)
                            client_activities[client_id] = activity
                        activity.start()
        elif activity is not None:
            activity.start()
        try:
            self._forward_lb(request, shared, parent_pid, activity)
        finally:
            if activity is not None:
                activity.end()
                if client_id is not None:
                    assert client_lock is not None and client_activities is not None
                    with client_lock:
                        if client_activities.get(client_id) is activity and activity.inflight == 0:
                            client_activities.pop(client_id, None)

    def _forward_lb(
        self,
        request: urllib.request.Request,
        shared: bool,
        parent_pid: int | None,
        activity: LbActivity | None,
    ) -> None:

        connect_retries = max(0, env_int("CLAUDE_LB_SHIM_CONNECT_RETRIES", SHIM_CONNECT_RETRIES_DEFAULT))
        backoff = max(0.05, env_float("CLAUDE_LB_SHIM_CONNECT_BACKOFF", SHIM_CONNECT_BACKOFF_DEFAULT))
        backoff_cap = max(backoff, env_float("CLAUDE_LB_SHIM_CONNECT_BACKOFF_CAP", SHIM_CONNECT_BACKOFF_CAP_DEFAULT))
        attempt = 0
        while True:
            try:
                response = urllib.request.urlopen(request, timeout=600)
            except urllib.error.HTTPError as error:
                response = error
            except Exception as error:
                parent_alive = True
                if not shared:
                    try:
                        assert parent_pid is not None
                        os.kill(parent_pid, 0)
                    except OSError:
                        parent_alive = False
                if attempt < connect_retries and parent_alive and is_retryable_shim_connect_error(error):
                    time.sleep(min(backoff * (2**attempt), backoff_cap))
                    attempt += 1
                    continue
                self._send_shim_error(error)
                return
            if activity is not None:
                activity.progress()

            try:
                self._copy_response(response, activity)
            finally:
                response.close()
            return

    def _is_websocket_upgrade(self) -> bool:
        connection_tokens = {
            token.strip().lower() for value in self.headers.get_all("connection") or [] for token in value.split(",")
        }
        return "upgrade" in connection_tokens and (self.headers.get("upgrade") or "").strip().lower() == "websocket"

    def _tunnel_upgrade(self) -> None:
        self.close_connection = True
        upstream = None
        try:
            raw = socket.create_connection(DIRECT_UPGRADE_ADDRESS, timeout=20)
            upstream = direct_upgrade_tls_context().wrap_socket(raw, server_hostname=DIRECT_UPGRADE_HOST)
            request = f"{self.command} {self.path} HTTP/1.1\r\n" + "".join(
                f"{key}: {value}\r\n" for key, value in self.headers.items()
            )
            upstream.sendall(request.encode("latin-1") + b"\r\n")
            upstream.settimeout(None)
        except (OSError, UnicodeEncodeError) as error:
            if upstream is not None:
                try:
                    upstream.close()
                except OSError:
                    pass
            self._send_shim_error(error)
            return
        self.wfile.flush()
        splice(self.connection, upstream)

    def _forward_direct(self, body: bytes | None, headers: dict[str, str]) -> None:

        connection: http.client.HTTPSConnection | None = None
        try:
            connection = http.client.HTTPSConnection(
                "api.anthropic.com",
                443,
                timeout=600,
                context=ssl.create_default_context(),
            )
            connection.request(self.command, self.path, body=body, headers=headers)
            self._copy_response(connection.getresponse())
        except Exception as error:
            self._send_shim_error(error)
        finally:
            if connection is not None:
                connection.close()

    def _send_shim_error(self, error: BaseException) -> None:
        message = json.dumps({"error": {"message": str(error), "code": "claude_lb_shim_error"}}).encode()
        self.send_response(502)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(message)))
        self.send_header("connection", "close")
        self.end_headers()
        self.wfile.write(message)
        self.close_connection = True

    def _copy_response(
        self, response: http.client.HTTPResponse | urllib.error.HTTPError, activity: LbActivity | None = None
    ) -> None:
        status = getattr(response, "status", None) or getattr(response, "code", 502)
        content_type = response.headers.get("content-type", "")
        self.send_response(int(status))
        for key, value in response.headers.items():
            header = key.lower()
            if header not in HOP_BY_HOP_HEADERS and header != "content-length":
                self.send_header(key, value)
        self.send_header("connection", "close")
        self.end_headers()
        if "text/event-stream" in content_type:
            while True:
                line = response.readline()
                if not line:
                    break
                self.wfile.write(line)
                self.wfile.flush()
                if activity is not None:
                    activity.progress()
        else:
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
                if activity is not None:
                    activity.progress()
        self.close_connection = True


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
            tls = cast(ThreadingProxyServer, self.server).tls_context.wrap_socket(raw, server_side=True)
        except (OSError, ValueError):
            try:
                raw.close()
            except OSError:
                pass
            return
        try:
            LbApiHandler(tls, ("127.0.0.1", 0), cast(ThreadingProxyServer, self.server))
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
        sent, received = splice(
            self.connection, upstream, getattr(cast(ThreadingProxyServer, self.server), "tunnel_bucket", None)
        )
        if connect_elapsed >= SHIM_TUNNEL_SLOW_CONNECT_SECONDS:
            log_tunnel_connect(host, port, connect_elapsed, "slow")
        if sent + received >= SHIM_TUNNEL_LOG_MIN_BYTES:
            duration = max(time.monotonic() - started, 0.001)
            queue_tunnel_log(
                f"cc-proxy: tunnel {safe_tunnel_target(host, port)} relayed "
                f"{received / 1e6:.1f}MB down / {sent / 1e6:.1f}MB up in {duration:.0f}s\n"
            )


class ThreadingProxyServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    tls_context: ssl.SSLContext
    tunnel_bucket: TunnelBucket | None
    session_id: str
    parent_pid: int | None
    shared: bool
    tag_headers: dict[str, str]
    ccgpt_mode: bool
    upstream_base_url: str
    activity: LbActivity | None
    client_activity: dict[str, LbActivity]
    client_activity_lock: threading.Lock

    daemon_threads = True
    allow_reuse_address = True

    request_queue_size = 1024

    def handle_error(self, request: object, client_address: object) -> None:

        return
