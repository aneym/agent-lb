from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import socket
import ssl
import struct
import threading
import time
from pathlib import Path

import pytest

from tests.unit.test_claude_lb_launch import load_launcher_module

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def read_head(sock: socket.socket) -> tuple[bytes, bytes]:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    return head, rest


def recv_exact(sock: socket.socket, n: int, buffered: bytearray) -> bytes:
    while len(buffered) < n:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("socket closed mid-frame")
        buffered.extend(chunk)
    out = bytes(buffered[:n])
    del buffered[:n]
    return out


def read_frame(sock: socket.socket, buffered: bytearray) -> tuple[int, bytes]:
    first, second = recv_exact(sock, 2, buffered)
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", recv_exact(sock, 2, buffered))[0]
    elif length == 127:
        length = struct.unpack("!Q", recv_exact(sock, 8, buffered))[0]
    mask = recv_exact(sock, 4, buffered) if second & 0x80 else b""
    payload = recv_exact(sock, length, buffered)
    if mask:
        payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
    return first & 0x0F, payload


def encode_frame(payload: bytes, *, masked: bool) -> bytes:
    header = bytes([0x81])
    mask_bit = 0x80 if masked else 0
    if len(payload) < 126:
        header += bytes([mask_bit | len(payload)])
    else:
        header += bytes([mask_bit | 126]) + struct.pack("!H", len(payload))
    if not masked:
        return header + payload
    mask = os.urandom(4)
    return header + mask + bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))


class FakeWebsocketUpstream:
    def __init__(self, certfile: Path, keyfile: Path) -> None:
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(certfile=str(certfile), keyfile=str(keyfile))
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.handshakes: list[dict[str, str]] = []
        self.request_lines: list[str] = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                raw, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(raw,), daemon=True).start()

    def _handle(self, raw: socket.socket) -> None:
        with self.context.wrap_socket(raw, server_side=True) as conn:
            head, rest = read_head(conn)
            lines = head.decode("latin-1").split("\r\n")
            self.request_lines.append(lines[0])
            headers = {}
            for line in lines[1:]:
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()
            self.handshakes.append(headers)
            if headers.get("upgrade", "").lower() != "websocket":
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}")
                return
            accept = base64.b64encode(hashlib.sha1((headers["sec-websocket-key"] + WS_GUID).encode()).digest()).decode()
            conn.sendall(
                (
                    "HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {accept}\r\n"
                    f"Sec-WebSocket-Protocol: {headers.get('sec-websocket-protocol', '')}\r\n\r\n"
                ).encode()
            )
            buffered = bytearray(rest)
            while True:
                try:
                    opcode, payload = read_frame(conn, buffered)
                except (ConnectionError, OSError):
                    return
                if opcode == 0x8:
                    return
                conn.sendall(encode_frame(b"echo:" + payload, masked=False))

    def close(self) -> None:
        self.listener.close()


class FakeLb(http.server.ThreadingHTTPServer):
    daemon_threads = True


def start_fake_lb() -> tuple[FakeLb, list[dict]]:
    seen: list[dict] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("content-length", "0")))
            seen.append({"path": self.path, "headers": dict(self.headers.items()), "body": body})
            reply = json.dumps({"type": "message", "accounted": True}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

    server = FakeLb(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, seen


def trust_local_ca(ca_cert: Path) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=str(ca_cert))
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context


def mitm_connect(proxy_port: int, ca_cert: Path) -> ssl.SSLSocket:
    sock = socket.create_connection(("127.0.0.1", proxy_port), timeout=10)
    sock.sendall(b"CONNECT api.anthropic.com:443 HTTP/1.1\r\nHost: api.anthropic.com:443\r\n\r\n")
    head, _ = read_head(sock)
    assert head.startswith(b"HTTP/1.1 200")
    return trust_local_ca(ca_cert).wrap_socket(sock, server_hostname="api.anthropic.com")


@pytest.fixture
def proxy(monkeypatch, tmp_path):
    launcher = load_launcher_module()
    tls_dir = tmp_path / "tls"
    monkeypatch.setattr(launcher, "TLS_DIR", tls_dir)
    monkeypatch.setattr(launcher, "CA_CERT", tls_dir / "ca.pem")
    monkeypatch.setattr(launcher, "CA_KEY", tls_dir / "ca-key.pem")
    monkeypatch.setattr(launcher, "LEAF_CERT", tls_dir / "leaf.pem")
    monkeypatch.setattr(launcher, "LEAF_KEY", tls_dir / "leaf-key.pem")
    monkeypatch.setattr(launcher, "CERT_MARKER", tls_dir / ".v1")
    monkeypatch.setenv("HOME", str(tmp_path))
    launcher.ensure_mitm_certs()

    upstream = FakeWebsocketUpstream(launcher.LEAF_CERT, launcher.LEAF_KEY)
    monkeypatch.setattr(launcher, "DIRECT_UPGRADE_ADDRESS", ("127.0.0.1", upstream.port))
    monkeypatch.setattr(
        launcher,
        "_direct_upgrade_tls_context",
        lambda: trust_local_ca(launcher.CA_CERT),
    )
    lb, lb_seen = start_fake_lb()

    ready = tmp_path / "proxy.ready"
    threading.Thread(
        target=launcher.run_lb_proxy,
        args=(str(ready), "", None, f"http://127.0.0.1:{lb.server_address[1]}"),
        kwargs={"shared": True},
        daemon=True,
    ).start()
    deadline = time.monotonic() + 10
    while not ready.exists():
        assert time.monotonic() < deadline, "proxy never became ready"
        time.sleep(0.05)
    port = int(ready.read_text().strip())
    yield launcher, port, upstream, lb_seen
    upstream.close()
    lb.shutdown()


def test_websocket_upgrade_on_intercepted_host_tunnels_and_messages_still_reach_lb(proxy) -> None:
    launcher, port, upstream, lb_seen = proxy

    ws = mitm_connect(port, launcher.CA_CERT)
    key = base64.b64encode(os.urandom(16)).decode()
    ws.sendall(
        (
            "GET /api/frame/sync?slug=probe HTTP/1.1\r\n"
            "Host: api.anthropic.com\r\n"
            "Connection: Upgrade\r\n"
            "Upgrade: websocket\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Protocol: frame-sync.v1\r\n"
            "Authorization: Bearer artifact-token\r\n\r\n"
        ).encode()
    )
    head, rest = read_head(ws)
    status_line, *header_lines = head.decode("latin-1").split("\r\n")
    response_headers = {k.strip().lower(): v.strip() for k, _, v in (h.partition(":") for h in header_lines)}
    assert status_line.startswith("HTTP/1.1 101")
    assert (
        response_headers["sec-websocket-accept"]
        == base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
    )
    assert response_headers["sec-websocket-protocol"] == "frame-sync.v1"

    forwarded = upstream.handshakes[-1]
    assert upstream.request_lines[-1] == "GET /api/frame/sync?slug=probe HTTP/1.1"
    assert forwarded["connection"] == "Upgrade"
    assert forwarded["upgrade"] == "websocket"
    assert forwarded["sec-websocket-key"] == key
    assert forwarded["authorization"] == "Bearer artifact-token"
    assert forwarded["host"] == "api.anthropic.com"

    buffered = bytearray(rest)
    for text in (b"hello", b"x" * 300):
        ws.sendall(encode_frame(text, masked=True))
        opcode, payload = read_frame(ws, buffered)
        assert (opcode, payload) == (0x1, b"echo:" + text)
    ws.close()

    api = mitm_connect(port, launcher.CA_CERT)
    body = b'{"model":"claude-opus","messages":[]}'
    api.sendall(
        b"POST /v1/messages HTTP/1.1\r\n"
        b"Host: api.anthropic.com\r\n"
        + f"Content-Length: {len(body)}\r\n".encode()
        + b"Content-Type: application/json\r\n"
        + b"X-Claude-Session-Id: desktop-session\r\n"
        + b"Connection: close\r\n\r\n"
        + body
    )
    response = b""
    while chunk := api.recv(4096):
        response += chunk
    api.close()

    assert response.startswith(b"HTTP/1.1 200")
    assert response.endswith(b'{"type": "message", "accounted": true}')
    assert len(lb_seen) == 1
    assert lb_seen[0]["path"] == "/v1/messages"
    assert lb_seen[0]["body"] == body
    assert lb_seen[0]["headers"].get("X-Claude-Session-Id") == "desktop-session"
    assert len(upstream.handshakes) == 1
