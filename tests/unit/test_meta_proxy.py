

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


class _KeyedUpstream(BaseHTTPRequestHandler):
    seen: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length))
        auth = self.headers.get("Authorization")
        type(self).seen.append({"auth": auth, "model": payload.get("model"), "headers": dict(self.headers)})
        if auth != "Bearer fresh-key":
            self.send_response(401)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")
            return
        body = b'{"id":"r1","usage":{"input_tokens":11,"output_tokens":2}}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


class _Keys:
    def __init__(self, values: list[str | None]) -> None:
        self.values = values

    async def get(self, *, refresh: bool = False) -> str | None:
        if refresh and len(self.values) > 1:
            self.values.pop(0)
        return self.values[0]


@pytest.fixture()
def provider(monkeypatch: pytest.MonkeyPatch, tmp_path):
    _KeyedUpstream.seen = []
    server = HTTPServer(("127.0.0.1", 0), _KeyedUpstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(meta_service, "UPSTREAM_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    table = tmp_path / "routing-table.json"
    table.write_text(json.dumps({"aliases": {"muse-latest": {"pinned": "muse-wonder-9"}}}))
    monkeypatch.setenv("ROUTE_TABLE", str(table))
    logged: list[dict] = []

    async def record(**kwargs):  # type: ignore[no-untyped-def]
        logged.append(kwargs)

    monkeypatch.setattr(meta_service, "_log_call", record)
    app = FastAPI()
    app.include_router(meta_service.provider_router)
    yield monkeypatch, logged, TestClient(app)
    server.shutdown()


def test_provider_injects_managed_key_and_refreshes_once(provider) -> None:
    monkeypatch, logged, client = provider
    monkeypatch.setattr(meta_service, "credential", _Keys(["stale-key", "fresh-key"]))
    response = client.post(
        "/muse/v1/responses",
        headers={"Authorization": "Bearer agent-lb", "x-agent-lb-seat": "sunday", "x-tbh-session-id": "s-9"},
        json={"model": "muse-latest", "input": "hi"},
    )
    assert response.status_code == 200
    assert [call["auth"] for call in _KeyedUpstream.seen] == ["Bearer stale-key", "Bearer fresh-key"]
    assert {call["model"] for call in _KeyedUpstream.seen} == {"muse-wonder-9"}
    assert not any(k.lower().startswith("x-agent-lb-") for k in _KeyedUpstream.seen[-1]["headers"])
    assert [(c["model"], c["status"], c["transport"], c["session_id"]) for c in logged] == [
        ("muse-wonder-9", "success", "muse-provider", "s-9")
    ]
    assert meta_service.parse_usage_from_sse_tail(logged[0]["tail"]) == (11, 2)


@pytest.mark.parametrize(
    ("model", "keys", "status", "code"),
    [
        ("muse-wonder-1.0-contributor", ["fresh-key"], 400, "contributor_mode_refused"),
        ("gpt-6.1-sol", ["fresh-key"], 400, "unknown_muse_model"),
        ("muse-wonder-1.0", [None], 503, "muse_credential_unavailable"),
    ],
)
def test_provider_refusals_never_reach_upstream(provider, model, keys, status, code) -> None:
    monkeypatch, _, client = provider
    monkeypatch.setattr(meta_service, "credential", _Keys(keys))
    response = client.post("/muse/v1/responses", json={"model": model, "input": "hi"})
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert _KeyedUpstream.seen == []
