from __future__ import annotations

import pytest

import app.modules.proxy.service as proxy_module
from app.core.clients.proxy import ProxyResponseError
from tests.integration.proxy_api_extended_support import (
    _force_usage_weighted_routing as _force_usage_weighted_routing,
)
from tests.integration.proxy_api_extended_support import (
    _import_account,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_thread_goal_get_forwards_upstream_goal(async_client, monkeypatch, method):
    await _import_account(async_client, "acc_goal_get", "goal-get@example.com")
    calls = []

    async def fake_thread_goal(
        operation,
        payload,
        headers,
        access_token,
        account_id,
        *,
        method="POST",
        timeout_seconds=None,
        **_kwargs,
    ):
        calls.append(
            {
                "operation": operation,
                "payload": dict(payload),
                "access_token": access_token,
                "account_id": account_id,
                "method": method,
                "timeout_seconds": timeout_seconds,
                "session_id": headers.get("session_id"),
            }
        )
        return {
            "goal": {
                "threadId": payload["threadId"],
                "objective": "ship the proxy",
                "status": "active",
                "tokenBudget": None,
                "tokensUsed": 0,
                "timeBudgetSeconds": None,
                "timeUsedSeconds": 0,
                "createdAt": 1,
                "updatedAt": 1,
            }
        }

    monkeypatch.setattr(proxy_module, "core_thread_goal_request", fake_thread_goal)
    thread_id = "019debd9-2372-7f23-92b9-9f34002a6355"
    response = await async_client.request(
        method,
        "/backend-api/codex/thread/goal/get",
        params={"threadId": thread_id} if method == "GET" else None,
        json={"threadId": thread_id} if method == "POST" else None,
        headers={"session_id": "goal-session"},
    )

    assert response.status_code == 200
    assert response.json()["goal"]["objective"] == "ship the proxy"
    assert calls == [
        {
            "operation": "get",
            "payload": {"threadId": thread_id},
            "access_token": "access-token",
            "account_id": "acc_goal_get",
            "method": method,
            "timeout_seconds": calls[0]["timeout_seconds"],
            "session_id": "goal-session",
        }
    ]
    assert isinstance(calls[0]["timeout_seconds"], float)
    assert calls[0]["timeout_seconds"] > 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "operation", "payload", "expected"),
    [
        (
            "/backend-api/codex/thread/goal/set",
            "set",
            {
                "threadId": "019debd9-2372-7f23-92b9-9f34002a6355",
                "objective": "ship the whole protocol",
                "status": "active",
            },
            {"goal": {"threadId": "019debd9-2372-7f23-92b9-9f34002a6355", "objective": "ship the whole protocol"}},
        ),
        (
            "/backend-api/codex/thread/goal/clear",
            "clear",
            {"threadId": "019debd9-2372-7f23-92b9-9f34002a6355"},
            {"cleared": True},
        ),
    ],
)
async def test_thread_goal_mutations_forward_upstream(
    async_client,
    monkeypatch,
    endpoint,
    operation,
    payload,
    expected,
):
    await _import_account(async_client, f"acc_goal_{operation}", f"goal-{operation}@example.com")
    calls = []

    async def fake_thread_goal(
        current_operation,
        current_payload,
        headers,
        access_token,
        account_id,
        *,
        method="POST",
        timeout_seconds=None,
        **_kwargs,
    ):
        calls.append((current_operation, dict(current_payload), access_token, account_id, method, timeout_seconds))
        return expected

    monkeypatch.setattr(proxy_module, "core_thread_goal_request", fake_thread_goal)

    response = await async_client.post(endpoint, json=payload)

    assert response.status_code == 200
    assert response.json() == expected
    assert calls[0][:5] == (operation, payload, "access-token", f"acc_goal_{operation}", "POST")
    assert isinstance(calls[0][5], float)
    assert calls[0][5] > 0


@pytest.mark.asyncio
async def test_thread_goal_get_returns_empty_goal_when_upstream_lacks_protocol(async_client, monkeypatch):
    await _import_account(async_client, "acc_goal_missing", "goal-missing@example.com")

    async def fake_thread_goal(*_args, **_kwargs):
        raise ProxyResponseError(404, {"error": {"code": "not_found", "message": "Not Found"}})

    monkeypatch.setattr(proxy_module, "core_thread_goal_request", fake_thread_goal)

    response = await async_client.post(
        "/backend-api/codex/thread/goal/get",
        json={"threadId": "019debd9-2372-7f23-92b9-9f34002a6355"},
    )

    assert response.status_code == 200
    assert response.json() == {"goal": None}


@pytest.mark.asyncio
async def test_thread_goal_get_propagates_non_protocol_404(async_client, monkeypatch):
    await _import_account(async_client, "acc_goal_gateway_404", "goal-gateway-404@example.com")

    async def fake_thread_goal(*_args, **_kwargs):
        raise ProxyResponseError(
            404,
            {"error": {"code": "upstream_error", "message": "Upstream error: HTTP 404 Not Found"}},
        )

    monkeypatch.setattr(proxy_module, "core_thread_goal_request", fake_thread_goal)

    response = await async_client.post(
        "/backend-api/codex/thread/goal/get",
        json={"threadId": "019debd9-2372-7f23-92b9-9f34002a6355"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "upstream_error"


@pytest.mark.asyncio
async def test_thread_goal_get_propagates_thread_not_found(async_client, monkeypatch):
    await _import_account(async_client, "acc_goal_thread_not_found", "goal-thread-not-found@example.com")

    async def fake_thread_goal(*_args, **_kwargs):
        raise ProxyResponseError(
            404,
            {"error": {"code": "not_found", "message": "Thread not found"}},
        )

    monkeypatch.setattr(proxy_module, "core_thread_goal_request", fake_thread_goal)

    response = await async_client.post(
        "/backend-api/codex/thread/goal/get",
        json={"threadId": "019debd9-2372-7f23-92b9-9f34002a6355"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_thread_goal_get_propagates_real_client_errors(async_client, monkeypatch):
    await _import_account(async_client, "acc_goal_rate_limited", "goal-rate@example.com")

    async def fake_thread_goal(*_args, **_kwargs):
        raise ProxyResponseError(
            429,
            {"error": {"code": "rate_limit_exceeded", "message": "slow down", "type": "rate_limit_error"}},
        )

    monkeypatch.setattr(proxy_module, "core_thread_goal_request", fake_thread_goal)

    response = await async_client.post(
        "/backend-api/codex/thread/goal/get",
        json={"threadId": "019debd9-2372-7f23-92b9-9f34002a6355"},
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_thread_goal_get_rejects_malformed_json(async_client):
    await _import_account(async_client, "acc_goal_bad_json", "goal-bad-json@example.com")

    response = await async_client.post(
        "/backend-api/codex/thread/goal/get",
        content=b'{"threadId":',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "thread goal payload must be valid JSON"


@pytest.mark.asyncio
async def test_thread_goal_get_rejects_malformed_utf8_json(async_client):
    await _import_account(async_client, "acc_goal_bad_utf8", "goal-bad-utf8@example.com")

    response = await async_client.post(
        "/backend-api/codex/thread/goal/get",
        content=b'{"threadId":"\xff"}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "thread goal payload must be valid JSON"


@pytest.mark.asyncio
async def test_thread_goal_get_propagates_selection_failures(async_client, monkeypatch):
    async def fake_select(*_args, **_kwargs):
        return proxy_module.AccountSelection(
            account=None,
            error_message="No scoped accounts are available",
            error_code="no_accounts",
        )

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select)

    response = await async_client.post(
        "/backend-api/codex/thread/goal/get",
        json={"threadId": "019debd9-2372-7f23-92b9-9f34002a6355"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "no_accounts"


@pytest.mark.asyncio
async def test_thread_goal_set_propagates_upstream_errors(async_client, monkeypatch):
    await _import_account(async_client, "acc_goal_set_error", "goal-set-error@example.com")

    async def fake_thread_goal(*_args, **_kwargs):
        raise ProxyResponseError(404, {"error": {"code": "not_found", "message": "Not Found"}})

    monkeypatch.setattr(proxy_module, "core_thread_goal_request", fake_thread_goal)

    response = await async_client.post(
        "/backend-api/codex/thread/goal/set",
        json={"threadId": "019debd9-2372-7f23-92b9-9f34002a6355", "objective": "keep real errors"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_thread_goal_retry_failure_after_forced_refresh_updates_account_health(async_client, monkeypatch):
    await _import_account(async_client, "acc_goal_retry_error", "goal-retry-error@example.com")
    calls = 0
    handled: list[tuple[str, int]] = []

    async def fake_thread_goal(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ProxyResponseError(401, {"error": {"code": "invalid_api_key", "message": "stale token"}})
        raise ProxyResponseError(
            429,
            {"error": {"code": "rate_limit_exceeded", "message": "still blocked", "type": "rate_limit_error"}},
        )

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        assert timeout_seconds is not None
        return account

    async def fake_handle_proxy_error(self, account, exc):
        handled.append((account.id, exc.status_code))

    monkeypatch.setattr(proxy_module, "core_thread_goal_request", fake_thread_goal)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)
    monkeypatch.setattr(proxy_module.ProxyService, "_handle_proxy_error", fake_handle_proxy_error)

    response = await async_client.post(
        "/backend-api/codex/thread/goal/set",
        json={"threadId": "019debd9-2372-7f23-92b9-9f34002a6355", "objective": "retry honestly"},
    )

    assert response.status_code == 429
    assert calls == 2
    assert len(handled) == 1
    assert handled[0][0].startswith("acc_goal_retry_error")
    assert handled[0][1] == 429


@pytest.mark.asyncio
async def test_thread_goal_repeated_401_after_refresh_fails_over(async_client, monkeypatch):
    await _import_account(async_client, "acc_goal_invalidated_a", "goal-invalidated-a@example.com")
    await _import_account(async_client, "acc_goal_invalidated_b", "goal-invalidated-b@example.com")
    captured_account_ids: list[str | None] = []
    invalidated_account_id: str | None = None

    async def fake_thread_goal(operation, payload, headers, access_token, account_id, **kwargs):
        del operation, payload, headers, access_token, kwargs
        nonlocal invalidated_account_id
        if invalidated_account_id is None:
            invalidated_account_id = account_id
        captured_account_ids.append(account_id)
        if account_id == invalidated_account_id:
            raise ProxyResponseError(401, {"error": {"code": "invalid_api_key", "message": "token invalidated"}})
        return {"goal": {"objective": "recovered"}}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        assert timeout_seconds is not None
        return account

    monkeypatch.setattr(proxy_module, "core_thread_goal_request", fake_thread_goal)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    response = await async_client.post(
        "/backend-api/codex/thread/goal/set",
        json={"threadId": "019debd9-2372-7f23-92b9-9f34002a6355", "objective": "recover"},
    )

    assert response.status_code == 200
    assert response.json()["goal"]["objective"] == "recovered"
    assert captured_account_ids[:2] == [invalidated_account_id, invalidated_account_id]
    assert captured_account_ids[2] != invalidated_account_id


@pytest.mark.asyncio
async def test_thread_goal_set_uses_active_account_when_budget_selection_is_empty(async_client, monkeypatch):
    await _import_account(async_client, "acc_goal_control", "goal-control@example.com")
    calls = []

    async def fake_select(*_args, **_kwargs):
        return proxy_module.AccountSelection(
            account=None,
            error_message="No active accounts available",
            error_code="no_accounts",
        )

    async def fake_thread_goal(
        operation,
        payload,
        headers,
        access_token,
        account_id,
        *,
        method="POST",
        timeout_seconds=None,
        **_kwargs,
    ):
        calls.append((operation, dict(payload), access_token, account_id, method, timeout_seconds))
        return {"cleared": True}

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select)
    monkeypatch.setattr(proxy_module, "core_thread_goal_request", fake_thread_goal)
    payload = {"threadId": "019debd9-2372-7f23-92b9-9f34002a6355"}

    response = await async_client.post("/backend-api/codex/thread/goal/clear", json=payload)

    assert response.status_code == 200
    assert response.json() == {"cleared": True}
    assert calls[0][:5] == ("clear", payload, "access-token", "acc_goal_control", "POST")
    assert isinstance(calls[0][5], float)
    assert calls[0][5] > 0
