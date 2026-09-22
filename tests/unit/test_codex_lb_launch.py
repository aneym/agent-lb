from __future__ import annotations

import base64
import importlib.machinery
import importlib.util
import os
import signal
import socket
import socketserver
import threading
import time
from pathlib import Path

import pytest


def load_launcher_module():
    path = Path(__file__).resolve().parents[2] / "clients" / "codex-lb-launch"
    loader = importlib.machinery.SourceFileLoader("codex_lb_launch_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class _OriginHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        request = bytearray()
        while b"\r\n\r\n" not in request:
            request.extend(self.request.recv(8192))
        line = bytes(request).split(b"\r\n", 1)[0]
        path = line.split(b" ", 2)[1]
        size = int(path.removeprefix(b"/bytes/")) if path.startswith(b"/bytes/") else 4
        body = b"x" * size if size != 4 else b"pong"
        self.request.sendall(
            f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )


class _ThreadingServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


def _start_server(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def _proxy_get(proxy_port: int, token: str, origin_port: int, path: str) -> bytes:
    authorization = base64.b64encode(f"codex:{token}".encode()).decode()
    with socket.create_connection(("127.0.0.1", proxy_port), timeout=10) as connection:
        connection.sendall(
            (
                f"GET http://127.0.0.1:{origin_port}{path} HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{origin_port}\r\n"
                f"Proxy-Authorization: Basic {authorization}\r\n"
                "Connection: close\r\n\r\n"
            ).encode()
        )
        response = bytearray()
        while chunk := connection.recv(64 * 1024):
            response.extend(chunk)
    return bytes(response)


def test_tunnel_bucket_uses_configured_aggregate_rate_and_bounded_burst(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = load_launcher_module()
    monkeypatch.setenv("CODEX_LB_TUNNEL_RATE_MBPS", "40")
    monkeypatch.setenv("CODEX_LB_TUNNEL_BURST_SECONDS", "0.05")

    bucket = launcher.tunnel_bucket_from_env()

    assert bucket is not None
    assert bucket.rate == pytest.approx(5_000_000.0)
    assert bucket.capacity == pytest.approx(250_000.0)


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_tunnel_bucket_can_be_explicitly_disabled(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    launcher = load_launcher_module()
    monkeypatch.setenv("CODEX_LB_TUNNEL_RATE_MBPS", value)

    assert launcher.tunnel_bucket_from_env() is None


def test_plain_http_forwarding_and_proxy_authentication() -> None:
    launcher = load_launcher_module()
    origin = _ThreadingServer(("127.0.0.1", 0), _OriginHandler)
    proxy = launcher._ProxyServer(
        ("127.0.0.1", 0), launcher.ProxyState(None, "secret")
    )
    _start_server(origin)
    _start_server(proxy)
    try:
        response = _proxy_get(
            proxy.server_address[1], "secret", origin.server_address[1], "/ping"
        )
        assert response.startswith(b"HTTP/1.1 200")
        assert response.endswith(b"pong")

        with socket.create_connection(("127.0.0.1", proxy.server_address[1])) as connection:
            connection.sendall(
                f"GET http://127.0.0.1:{origin.server_address[1]}/ping HTTP/1.1\r\n"
                "Host: localhost\r\n\r\n".encode()
            )
            assert connection.recv(64).startswith(b"HTTP/1.1 407")
    finally:
        proxy.shutdown()
        origin.shutdown()
        proxy.server_close()
        origin.server_close()


def test_shared_proxy_caps_concurrent_bulk_and_keeps_tiny_request_responsive() -> None:
    launcher = load_launcher_module()
    rate = 5_000_000.0
    burst_seconds = 0.05
    origin = _ThreadingServer(("127.0.0.1", 0), _OriginHandler)
    proxy = launcher._ProxyServer(
        ("127.0.0.1", 0),
        launcher.ProxyState(launcher.FairTokenBucket(rate, burst_seconds), "secret"),
    )
    _start_server(origin)
    _start_server(proxy)
    payload_each = 8_000_000
    responses: list[bytes] = []

    def fetch_bulk() -> None:
        responses.append(
            _proxy_get(
                proxy.server_address[1],
                "secret",
                origin.server_address[1],
                f"/bytes/{payload_each}",
            )
        )

    started = time.monotonic()
    workers = [threading.Thread(target=fetch_bulk) for _ in range(2)]
    for worker in workers:
        worker.start()
    time.sleep(0.2)
    tiny_started = time.monotonic()
    tiny = _proxy_get(proxy.server_address[1], "secret", origin.server_address[1], "/ping")
    tiny_elapsed = time.monotonic() - tiny_started
    for worker in workers:
        worker.join()
    elapsed = time.monotonic() - started
    try:
        steady_payload = 2 * payload_each - rate * burst_seconds
        assert steady_payload / elapsed <= rate * 1.08
        assert len(responses) == 2
        assert all(response.endswith(b"x" * 32) for response in responses)
        assert tiny.endswith(b"pong")
        assert tiny_elapsed < 0.25
    finally:
        proxy.shutdown()
        origin.shutdown()
        proxy.server_close()
        origin.server_close()


def test_connect_preserves_half_close_and_bidirectional_stream() -> None:
    launcher = load_launcher_module()

    class EchoAfterEof(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            data = bytearray()
            while chunk := self.request.recv(8192):
                data.extend(chunk)
            self.request.sendall(bytes(data).upper())

    origin = _ThreadingServer(("127.0.0.1", 0), EchoAfterEof)
    proxy = launcher._ProxyServer(
        ("127.0.0.1", 0), launcher.ProxyState(None, "secret")
    )
    _start_server(origin)
    _start_server(proxy)
    authorization = base64.b64encode(b"codex:secret").decode()
    try:
        with socket.create_connection(("127.0.0.1", proxy.server_address[1])) as connection:
            connection.sendall(
                (
                    f"CONNECT 127.0.0.1:{origin.server_address[1]} HTTP/1.1\r\n"
                    f"Proxy-Authorization: Basic {authorization}\r\n\r\n"
                ).encode()
            )
            response = bytearray()
            while b"\r\n\r\n" not in response:
                response.extend(connection.recv(1024))
            assert response.startswith(b"HTTP/1.1 200")
            connection.sendall(b"stream")
            connection.shutdown(socket.SHUT_WR)
            echoed = bytearray()
            while chunk := connection.recv(1024):
                echoed.extend(chunk)
            assert echoed == b"STREAM"
    finally:
        proxy.shutdown()
        origin.shutdown()
        proxy.server_close()
        origin.server_close()


def test_launcher_reuses_one_daemon_and_preserves_exec_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launcher = load_launcher_module()
    monkeypatch.setenv("CODEX_LB_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("CODEX_LB_TUNNEL_RATE_MBPS", "40")
    proxy_names = ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")
    for name in proxy_names:
        monkeypatch.delenv(name, raising=False)

    first_url, first_pid = launcher._ensure_proxy()
    try:
        time.sleep(0.4)
        second_url, second_pid = launcher._ensure_proxy()

        assert first_url == second_url
        assert first_pid == second_pid
        state = launcher._read_state(tmp_path / "proxy.json")
        assert state is not None
        assert launcher._public_status(state) == {
            "pid": first_pid,
            "port": state["port"],
            "rate_mbps": 40.0,
            "burst_seconds": 0.05,
            "healthy": True,
        }
        assert "token" not in launcher._public_status(state)
        calls: list[tuple[str, list[str]]] = []
        monkeypatch.setattr(launcher, "_ensure_proxy", lambda: (first_url, first_pid))
        monkeypatch.setattr(
            launcher.os, "execvp", lambda command, argv: calls.append((command, argv))
        )
        launcher.main(["exec", "--model", "gpt-6-astra"])
        assert calls == [("codex", ["codex", "exec", "--model", "gpt-6-astra"])]
        assert os.environ["HTTP_PROXY"] == first_url
        assert os.environ["HTTPS_PROXY"] == first_url
    finally:
        os.kill(first_pid, signal.SIGTERM)


def test_live_but_unavailable_daemon_fails_closed_without_starting_another(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launcher = load_launcher_module()
    monkeypatch.setenv("CODEX_LB_RUNTIME_DIR", str(tmp_path))
    state = {
        "version": launcher.STATE_VERSION,
        "pid": os.getpid(),
        "port": 1,
        "token": "unavailable",
        "rate_mbps": 40.0,
        "burst_seconds": 0.05,
    }
    launcher._write_state(tmp_path / "proxy.json", state)
    starts: list[object] = []
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *args, **kwargs: starts.append(args))

    with pytest.raises(RuntimeError, match="alive but unavailable"):
        launcher._ensure_proxy()

    assert starts == []


def test_existing_proxy_configuration_is_not_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = load_launcher_module()
    monkeypatch.setenv("HTTPS_PROXY", "http://existing-proxy:8080")

    with pytest.raises(SystemExit, match="refuses to replace"):
        launcher.main([])

    assert os.environ["HTTPS_PROXY"] == "http://existing-proxy:8080"
