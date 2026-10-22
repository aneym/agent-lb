from __future__ import annotations

import http.client
import http.server
import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from claude_lb_certs import LbActivity, routes_to_agent_lb  # pyrefly: ignore[missing-import]
from claude_lb_common import (  # pyrefly: ignore[missing-import]
    DIRECT_UPGRADE_ADDRESS,
    DIRECT_UPGRADE_HOST,
    HOP_BY_HOP_HEADERS,
    SHIM_CLIENT_IDLE_TIMEOUT_SECONDS,
    SHIM_CONNECT_BACKOFF_CAP_DEFAULT,
    SHIM_CONNECT_BACKOFF_DEFAULT,
    SHIM_CONNECT_RETRIES_DEFAULT,
    client_activity_path,
    direct_upgrade_tls_context,
    env_float,
    env_int,
    is_retryable_shim_connect_error,
    splice,
)
from claude_lb_models import CcgptModelViolation, ccgpt_upstream_path  # pyrefly: ignore[missing-import]


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
        parent_pid = self.server.parent_pid  # pyrefly: ignore[missing-attribute]
        session_id = self.server.session_id  # pyrefly: ignore[missing-attribute]
        upstream_base_url = self.server.upstream_base_url  # pyrefly: ignore[missing-attribute]
        shared = self.server.shared  # pyrefly: ignore[missing-attribute]

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
                headers.update(getattr(self.server, "tag_headers", {}))

        if not routes_to_lb:
            self._forward_direct(body, headers)
            return

        try:
            upstream_path = ccgpt_upstream_path(self.path, body) if self.server.ccgpt_mode else self.path  # pyrefly: ignore[missing-attribute]
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
        activity = getattr(self.server, "activity", None)
        client_id = None
        if shared and activity is None:
            client_id = self.headers.get("x-claude-code-session-id")
            client_path = client_activity_path(client_id) if client_id is not None else None
            if client_path is not None and client_path.exists():
                client_activities = getattr(self.server, "client_activity", None)
                client_lock = getattr(self.server, "client_activity_lock", None)
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
                    with client_lock:  # pyrefly: ignore[bad-context-manager, unbound-name]
                        if client_activities.get(client_id) is activity and activity.inflight == 0:  # pyrefly: ignore[missing-attribute, unbound-name]
                            client_activities.pop(client_id, None)  # pyrefly: ignore[missing-attribute]

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

    def _copy_response(self, response: object, activity: LbActivity | None = None) -> None:
        status = getattr(response, "status", None) or getattr(response, "code", 502)
        content_type = response.headers.get("content-type", "")  # pyrefly: ignore[missing-attribute]
        self.send_response(int(status))
        for key, value in response.headers.items():  # pyrefly: ignore[missing-attribute]
            header = key.lower()
            if header not in HOP_BY_HOP_HEADERS and header != "content-length":
                self.send_header(key, value)
        self.send_header("connection", "close")
        self.end_headers()
        if "text/event-stream" in content_type:
            while True:
                line = response.readline()  # pyrefly: ignore[missing-attribute]
                if not line:
                    break
                self.wfile.write(line)
                self.wfile.flush()
                if activity is not None:
                    activity.progress()
        else:
            while True:
                chunk = response.read(65536)  # pyrefly: ignore[missing-attribute]
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
                if activity is not None:
                    activity.progress()
        self.close_connection = True
