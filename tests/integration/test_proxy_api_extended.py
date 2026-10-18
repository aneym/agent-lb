from __future__ import annotations

import json

import pytest

import app.modules.proxy.service as proxy_module
from app.core.clients import proxy as core_proxy
from app.core.clients.proxy import ProxyResponseError
from tests.integration.proxy_api_extended_support import (
    _force_usage_weighted_routing as _force_usage_weighted_routing,
)
from tests.integration.proxy_api_extended_support import (
    _import_account,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_proxy_compact_not_implemented(async_client, monkeypatch):
    await _import_account(async_client, "acc_compact_ni", "ni@example.com")

    async def fake_compact(*_args, **_kwargs):
        raise NotImplementedError

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "not_implemented"


@pytest.mark.asyncio
async def test_proxy_compact_upstream_error_propagates(async_client, monkeypatch):
    await _import_account(async_client, "acc_compact_err", "err@example.com")

    async def fake_compact(*_args, **_kwargs):
        raise ProxyResponseError(502, {"error": {"code": "upstream_error", "message": "boom"}})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_error"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "upstream_path", "payload"),
    [
        ("/backend-api/codex/analytics-events/events", "analytics-events/events", {"events": []}),
        (
            "/backend-api/codex/memories/trace_summarize",
            "memories/trace_summarize",
            {"model": "gpt-5.1", "raw_memories": []},
        ),
        (
            "/backend-api/codex/safety/arc",
            "safety/arc",
            {"decision": "allow"},
        ),
    ],
)
async def test_codex_control_json_endpoints_forward_upstream(
    async_client,
    monkeypatch,
    endpoint,
    upstream_path,
    payload,
):
    await _import_account(async_client, "acc_codex_control", "codex-control@example.com")
    calls = []

    async def fake_codex_control_request(
        path,
        *,
        method,
        payload: bytes | None,
        query_params,
        headers,
        access_token,
        account_id,
        timeout_seconds=None,
        **_kwargs,
    ):
        calls.append(
            {
                "path": path,
                "method": method,
                "payload": json.loads(payload or b"{}"),
                "query_params": dict(query_params),
                "session_id": headers.get("session_id"),
                "access_token": access_token,
                "account_id": account_id,
                "timeout_seconds": timeout_seconds,
            }
        )
        return core_proxy.CodexControlResponse(
            status_code=200,
            body=json.dumps({"ok": True}).encode("utf-8"),
            headers={"content-type": "application/json", "x-request-id": "upstream-request"},
        )

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)

    response = await async_client.post(endpoint, json=payload, headers={"session_id": "control-session"})

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert response.headers["x-request-id"] == "upstream-request"
    assert calls == [
        {
            "path": upstream_path,
            "method": "POST",
            "payload": payload,
            "query_params": {},
            "session_id": "control-session",
            "access_token": "access-token",
            "account_id": "acc_codex_control",
            "timeout_seconds": calls[0]["timeout_seconds"],
        }
    ]
    assert isinstance(calls[0]["timeout_seconds"], float)
    assert calls[0]["timeout_seconds"] > 0


@pytest.mark.asyncio
async def test_codex_realtime_call_forwards_raw_sdp_and_location(async_client, monkeypatch):
    await _import_account(async_client, "acc_codex_realtime", "codex-realtime@example.com")
    calls = []

    async def fake_codex_control_request(
        path,
        *,
        method,
        payload: bytes | None,
        query_params,
        headers,
        access_token,
        account_id,
        timeout_seconds=None,
        **_kwargs,
    ):
        calls.append((path, method, payload, headers.get("content-type"), access_token, account_id, timeout_seconds))
        return core_proxy.CodexControlResponse(
            status_code=201,
            body=b"v=answer\r\n",
            headers={"content-type": "application/sdp", "location": "/v1/realtime/calls/call_123"},
        )

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)

    response = await async_client.post(
        "/backend-api/codex/realtime/calls",
        content=b"v=offer\r\n",
        headers={"content-type": "application/sdp"},
    )

    assert response.status_code == 201
    assert response.content == b"v=answer\r\n"
    assert response.headers["location"] == "/v1/realtime/calls/call_123"
    assert calls == [
        (
            "realtime/calls",
            "POST",
            b"v=offer\r\n",
            "application/sdp",
            "access-token",
            "acc_codex_realtime",
            calls[0][6],
        )
    ]
    assert isinstance(calls[0][6], float)
    assert calls[0][6] > 0


@pytest.mark.asyncio
async def test_codex_control_retry_failure_after_forced_refresh_updates_account_health(async_client, monkeypatch):
    await _import_account(async_client, "acc_codex_retry_error", "codex-retry-error@example.com")
    calls = 0
    handled: list[tuple[str, int]] = []

    async def fake_codex_control_request(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ProxyResponseError(401, {"error": {"code": "invalid_api_key", "message": "stale token"}})
        raise ProxyResponseError(
            503,
            {"error": {"code": "upstream_unavailable", "message": "still down", "type": "server_error"}},
        )

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        assert timeout_seconds is not None
        return account

    async def fake_handle_proxy_error(self, account, exc):
        handled.append((account.id, exc.status_code))

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)
    monkeypatch.setattr(proxy_module.ProxyService, "_handle_proxy_error", fake_handle_proxy_error)

    response = await async_client.post(
        "/backend-api/codex/safety/arc",
        json={"decision": "allow"},
    )

    assert response.status_code == 503
    assert "X-App-Version" not in response.headers
    assert calls == 2
    assert len(handled) == 1
    assert handled[0][0].startswith("acc_codex_retry_error")
    assert handled[0][1] == 503


@pytest.mark.asyncio
async def test_codex_control_repeated_401_after_refresh_fails_over(async_client, monkeypatch):
    await _import_account(async_client, "acc_codex_invalidated_a", "codex-invalidated-a@example.com")
    await _import_account(async_client, "acc_codex_invalidated_b", "codex-invalidated-b@example.com")
    captured_account_ids: list[str | None] = []
    invalidated_account_id: str | None = None

    async def fake_codex_control_request(*_args, account_id=None, **_kwargs):
        nonlocal invalidated_account_id
        if invalidated_account_id is None:
            invalidated_account_id = account_id
        captured_account_ids.append(account_id)
        if account_id == invalidated_account_id:
            raise ProxyResponseError(401, {"error": {"code": "invalid_api_key", "message": "token invalidated"}})
        return proxy_module.CodexControlResponse(status_code=200, headers={}, body=b'{"ok":true}')

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        assert timeout_seconds is not None
        return account

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    response = await async_client.post("/backend-api/codex/safety/arc", json={"decision": "allow"})

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert captured_account_ids[:2] == [invalidated_account_id, invalidated_account_id]
    assert captured_account_ids[2] != invalidated_account_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "upstream_path"),
    [
        ("/backend-api/codex/agent-identities/jwks", "agent-identities/jwks"),
        ("/backend-api/wham/agent-identities/jwks", "wham/agent-identities/jwks"),
    ],
)
async def test_codex_agent_identity_jwks_routes_forward_upstream(async_client, monkeypatch, endpoint, upstream_path):
    await _import_account(async_client, "acc_codex_jwks", "codex-jwks@example.com")
    calls = []

    async def fake_codex_control_request(
        path,
        *,
        method,
        payload,
        query_params,
        headers,
        access_token,
        account_id,
        timeout_seconds=None,
        **_kwargs,
    ):
        calls.append((path, method, payload, list(query_params), access_token, account_id, timeout_seconds))
        return core_proxy.CodexControlResponse(
            status_code=200,
            body=b'{"keys":[]}',
            headers={
                "cache-control": "public, max-age=3600",
                "content-type": "application/json",
                "etag": '"jwks-v1"',
                "last-modified": "Sat, 16 May 2026 19:00:00 GMT",
            },
        )

    monkeypatch.setattr(proxy_module, "core_codex_control_request", fake_codex_control_request)

    response = await async_client.get(endpoint, params=[("kid", "test"), ("kid", "next")])

    assert response.status_code == 200
    assert response.json() == {"keys": []}
    assert response.headers["cache-control"] == "public, max-age=3600"
    assert response.headers["etag"] == '"jwks-v1"'
    assert response.headers["last-modified"] == "Sat, 16 May 2026 19:00:00 GMT"
    assert calls[0][:6] == (
        upstream_path,
        "GET",
        None,
        [("kid", "test"), ("kid", "next")],
        "access-token",
        "acc_codex_jwks",
    )
    assert isinstance(calls[0][6], float)
    assert calls[0][6] > 0
