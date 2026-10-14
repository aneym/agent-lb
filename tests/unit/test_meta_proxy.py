

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.proxy import meta_service


class _Upstream(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        body = (
            b'data: {"type":"response.created","response":{"id":"r1"}}\n\n'
            b'data: {"type":"response.completed","response":{"usage":'
            b'{"input_tokens": 120, "output_tokens": 7}}}\n\n'
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        body = b'{"models": []}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture()
def upstream(monkeypatch: pytest.MonkeyPatch):
    server = HTTPServer(("127.0.0.1", 0), _Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(
        meta_service, "UPSTREAM_BASE_URL", f"http://127.0.0.1:{server.server_port}"
    )
    logged: list[dict] = []

    async def record(**kwargs):  # type: ignore[no-untyped-def]
        logged.append(kwargs)

    monkeypatch.setattr(meta_service, "_log_call", record)
    yield logged
    server.shutdown()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(meta_service.router)
    return TestClient(app)


def test_responses_stream_passthrough_and_accounting(upstream: list[dict]) -> None:
    with _client() as client:
        response = client.post(
            "/muse-api/responses",
            headers={"Authorization": "Bearer [REDACTED]", "x-tbh-session-id": "s-1"},
            json={"model": "muse-wonder-1.0", "input": "hi"},
        )
    assert response.status_code == 200
    assert "response.completed" in response.text
    assert len(upstream) == 1
    call = upstream[0]
    assert call["model"] == "muse-wonder-1.0"
    assert call["session_id"] == "s-1"
    assert call["status"] == "success"
    assert call["upstream_status"] == 200


def test_models_passthrough_without_accounting(upstream: list[dict]) -> None:
    with _client() as client:
        response = client.get("/muse-api/muse-code/models")
    assert response.status_code == 200
    assert response.json() == {"models": []}
    assert upstream == []


def test_unlisted_path_is_not_proxied(upstream: list[dict]) -> None:
    with _client() as client:
        assert client.post("/muse-api/chat/completions", json={}).status_code == 404
        assert client.get("/muse-api/responses").status_code == 404


def test_usage_parser_reads_completed_event() -> None:
    tail = (
        b'data: {"type":"response.completed","response":{"usage":'
        b'{"input_tokens": 42, "output_tokens": 3}}}\n\n'
        b"data: [DONE]\n\n"
    )
    assert meta_service.parse_usage_from_sse_tail(tail) == (42, 3)
    assert meta_service.parse_usage_from_sse_tail(b"no usage here") == (None, None)
    assert json.loads(b'{"ok": true}') == {"ok": True}
