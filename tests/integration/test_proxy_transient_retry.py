from __future__ import annotations

import pytest

import app.modules.proxy.service as proxy_module
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from tests.integration.proxy_transient_retry_support import (
    _extract_events,
    _import_account,
    _server_error_sse_event,
    _sse_event,
    _stream_timeout_sse_event,
    _success_sse_event,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _force_usage_weighted_routing(async_client) -> None:
    current = await async_client.get("/api/settings")
    assert current.status_code == 200
    payload = current.json()
    payload["routingStrategy"] = "usage_weighted"
    response = await async_client.put("/api/settings", json=payload)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_stream_server_error_succeeds_on_second_try_same_account(async_client, monkeypatch):

    await _import_account(async_client, "acc_trans_1", "trans1@example.com")

    call_count = 0
    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        nonlocal call_count
        call_count += 1
        seen_account_ids.append(account_id)
        if call_count == 1:
            yield _server_error_sse_event()
            return
        yield _success_sse_event()

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    completed = [e for e in events if e.get("type") == "response.completed"]
    assert len(completed) == 1

    assert len(seen_account_ids) == 2
    assert seen_account_ids[0] == seen_account_ids[1]


@pytest.mark.asyncio
async def test_stream_timeout_succeeds_on_second_try_same_account(async_client, monkeypatch):

    await _import_account(async_client, "acc_trans_timeout", "timeout@example.com")

    call_count = 0
    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        nonlocal call_count
        call_count += 1
        seen_account_ids.append(account_id)
        if call_count == 1:
            yield _stream_timeout_sse_event()
            return
        yield _success_sse_event()

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    completed = [e for e in events if e.get("type") == "response.completed"]
    failed = [e for e in events if e.get("type") == "response.failed"]
    assert len(completed) == 1
    assert failed == []
    assert len(seen_account_ids) == 2
    assert seen_account_ids[0] == seen_account_ids[1]


@pytest.mark.asyncio
async def test_stream_server_error_exhausts_inner_retries_then_failover(async_client, monkeypatch):

    await _import_account(async_client, "acc_trans_fo_a", "fo_a@example.com")
    await _import_account(async_client, "acc_trans_fo_b", "fo_b@example.com")

    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_account_ids.append(account_id)
        if account_id == "acc_trans_fo_a":
            yield _server_error_sse_event()
            return
        yield _success_sse_event()

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    completed = [e for e in events if e.get("type") == "response.completed"]
    assert len(completed) == 1

    a_calls = [aid for aid in seen_account_ids if aid == "acc_trans_fo_a"]
    b_calls = [aid for aid in seen_account_ids if aid == "acc_trans_fo_b"]
    assert len(a_calls) == 3
    assert len(b_calls) >= 1


@pytest.mark.asyncio
async def test_stream_server_error_all_accounts_exhausted(async_client, monkeypatch):

    await _import_account(async_client, "acc_trans_all_a", "all_a@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield _server_error_sse_event()

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)

    last_event = events[-1] if events else {}
    assert last_event.get("type") in ("response.failed", "error")


@pytest.mark.asyncio
async def test_stream_server_error_succeeds_on_third_try(async_client, monkeypatch):

    await _import_account(async_client, "acc_trans_3rd", "trans3@example.com")

    call_count = 0
    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        nonlocal call_count
        call_count += 1
        seen_account_ids.append(account_id)
        if call_count <= 2:
            yield _server_error_sse_event()
            return
        yield _success_sse_event()

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    completed = [e for e in events if e.get("type") == "response.completed"]
    assert len(completed) == 1

    assert len(seen_account_ids) == 3
    assert len(set(seen_account_ids)) == 1


@pytest.mark.asyncio
async def test_stream_http_500_retries_same_account_then_succeeds(async_client, monkeypatch):

    await _import_account(async_client, "acc_http500_1", "http500@example.com")

    call_count = 0
    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        nonlocal call_count
        call_count += 1
        seen_account_ids.append(account_id)
        if call_count == 1:
            raise ProxyResponseError(
                500,
                openai_error("server_error", "An error occurred while processing your request."),
                failure_phase="status",
            )
        yield _success_sse_event()

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    completed = [e for e in events if e.get("type") == "response.completed"]
    assert len(completed) == 1

    assert len(seen_account_ids) == 2
    assert seen_account_ids[0] == seen_account_ids[1]


@pytest.mark.asyncio
async def test_stream_http_500_exhausts_then_failover(async_client, monkeypatch):

    await _import_account(async_client, "acc_h5fo_a", "h5fo_a@example.com")
    await _import_account(async_client, "acc_h5fo_b", "h5fo_b@example.com")

    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_account_ids.append(account_id)
        if account_id == "acc_h5fo_a":
            raise ProxyResponseError(
                500,
                openai_error("server_error", "Internal server error"),
                failure_phase="status",
            )
        yield _success_sse_event()

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    completed = [e for e in events if e.get("type") == "response.completed"]
    assert len(completed) == 1

    a_calls = [aid for aid in seen_account_ids if aid == "acc_h5fo_a"]
    b_calls = [aid for aid in seen_account_ids if aid == "acc_h5fo_b"]
    assert len(a_calls) == 3
    assert len(b_calls) >= 1


@pytest.mark.asyncio
async def test_stream_connect_phase_429_usage_limit_transparent_failover(async_client, monkeypatch):

    await _import_account(async_client, "acc_stream_429_a", "stream429a@example.com")
    await _import_account(async_client, "acc_stream_429_b", "stream429b@example.com")

    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_account_ids.append(account_id)
        if account_id == "acc_stream_429_a":
            raise ProxyResponseError(
                429,
                openai_error("usage_limit_reached", "usage limit reached"),
                failure_phase="status",
            )
        yield _success_sse_event("resp_stream_429_ok")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    completed = [e for e in events if e.get("type") == "response.completed"]
    failed = [e for e in events if e.get("type") == "response.failed"]
    assert len(completed) == 1
    assert len(failed) == 0
    assert seen_account_ids[:2] == ["acc_stream_429_a", "acc_stream_429_b"]


@pytest.mark.asyncio
async def test_stream_http_502_unknown_code_fails_over_to_second_account(async_client, monkeypatch):
    await _import_account(async_client, "acc_h502_a", "h502_a@example.com")
    await _import_account(async_client, "acc_h502_b", "h502_b@example.com")

    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_account_ids.append(account_id)
        if account_id == "acc_h502_a":
            raise ProxyResponseError(
                502,
                openai_error("bad_gateway", "Bad gateway"),
                failure_phase="status",
            )
        yield _success_sse_event()

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    completed = [e for e in events if e.get("type") == "response.completed"]
    assert len(completed) == 1
    assert seen_account_ids[:2] == ["acc_h502_a", "acc_h502_b"]


@pytest.mark.asyncio
async def test_stream_non_server_error_not_retried_as_transient(async_client, monkeypatch):

    await _import_account(async_client, "acc_no_trans", "notrans@example.com")

    call_count = 0

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        nonlocal call_count
        call_count += 1
        yield _sse_event(
            {
                "type": "response.failed",
                "response": {
                    "error": {
                        "code": "invalid_request_error",
                        "message": "bad request",
                    },
                },
            }
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    failed = [e for e in events if e.get("type") == "response.failed"]
    assert len(failed) == 1

    assert call_count == 1


@pytest.mark.asyncio
async def test_stream_rate_limit_on_last_attempt_returns_actual_error(async_client, monkeypatch):

    await _import_account(async_client, "acc_rl_a", "rla@example.com")
    await _import_account(async_client, "acc_rl_b", "rlb@example.com")
    await _import_account(async_client, "acc_rl_c", "rlc@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield _sse_event(
            {
                "type": "response.failed",
                "response": {
                    "error": {
                        "code": "rate_limit_exceeded",
                        "message": "slow down",
                    },
                },
            }
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)

    last_event = events[-1] if events else {}
    error = last_event.get("response", {}).get("error", {})
    assert error.get("code") != "no_accounts", "Client received generic no_accounts instead of actual error"
