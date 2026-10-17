from __future__ import annotations

import base64
import json

import pytest

import app.modules.proxy.service as proxy_module
from app.core.auth import generate_unique_account_id
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.core.openai.models import CompactResponsePayload
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _force_usage_weighted_routing(async_client) -> None:
    current = await async_client.get("/api/settings")
    assert current.status_code == 200
    payload = current.json()
    payload["routingStrategy"] = "usage_weighted"
    response = await async_client.put("/api/settings", json=payload)
    assert response.status_code == 200


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _make_auth_json(account_id: str, email: str) -> dict:
    payload = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    return {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "accountId": account_id,
        },
    }


async def _import_account(async_client, account_id: str, email: str) -> str:
    auth_json = _make_auth_json(account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    return generate_unique_account_id(account_id, email)


def _sse_event(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _server_error_sse_event() -> str:
    return _sse_event(
        {
            "type": "response.failed",
            "response": {
                "error": {
                    "code": "server_error",
                    "message": "An error occurred while processing your request.",
                },
            },
        }
    )


def _stream_timeout_sse_event() -> str:
    return _sse_event(
        {
            "type": "response.failed",
            "response": {
                "error": {
                    "code": "upstream_request_timeout",
                    "message": "Proxy request budget exhausted",
                },
            },
        }
    )


def _stream_incomplete_sse_event(response_id: str = "resp_incomplete") -> str:
    return _sse_event(
        {
            "type": "response.failed",
            "response": {
                "id": response_id,
                "status": "failed",
                "error": {
                    "code": "stream_incomplete",
                    "message": "Upstream websocket closed before response.completed",
                },
            },
        }
    )


def _success_sse_event(response_id: str = "resp_ok") -> str:
    return _sse_event(
        {
            "type": "response.completed",
            "response": {
                "id": response_id,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        }
    )


def _extract_events(lines: list[str]) -> list[dict]:
    events = []
    for line in lines:
        if line.startswith("data: "):
            data = line[6:]
            if data == "[DONE]":
                continue
            events.append(json.loads(data))
    return events


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


@pytest.mark.asyncio
async def test_stream_mid_stream_error_is_surfaced_without_failover(async_client, monkeypatch):

    await _import_account(async_client, "acc_midstream_a", "midstreama@example.com")
    await _import_account(async_client, "acc_midstream_b", "midstreamb@example.com")

    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_account_ids.append(account_id)
        if account_id == "acc_midstream_a":
            yield _sse_event({"type": "response.in_progress", "response": {"id": "resp_midstream"}})
            yield _sse_event(
                {
                    "type": "response.failed",
                    "response": {"error": {"code": "rate_limit_exceeded", "message": "mid-stream limit"}},
                }
            )
            return
        yield _success_sse_event("resp_should_not_happen")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    failed = [e for e in events if e.get("type") == "response.failed"]
    completed = [e for e in events if e.get("type") == "response.completed"]
    assert len(failed) == 1
    assert failed[0].get("response", {}).get("error", {}).get("code") == "rate_limit_exceeded"
    assert len(completed) == 0
    assert seen_account_ids == ["acc_midstream_a"]


@pytest.mark.asyncio
async def test_v1_responses_protocol_only_truncation_retries_before_public_output(async_client, monkeypatch):

    await _import_account(async_client, "acc_public_protocol", "public-protocol@example.com")

    call_count = 0

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield _sse_event(
                {
                    "type": "response.created",
                    "response": {"id": "resp_protocol_failed", "object": "response", "status": "in_progress"},
                }
            )
            yield _sse_event(
                {
                    "type": "response.in_progress",
                    "response": {"id": "resp_protocol_failed", "object": "response", "status": "in_progress"},
                }
            )
            yield _stream_incomplete_sse_event("resp_protocol_failed")
            return
        yield _sse_event(
            {
                "type": "response.created",
                "response": {"id": "resp_protocol_ok", "object": "response", "status": "in_progress", "output": []},
            }
        )
        yield _sse_event({"type": "response.output_text.delta", "delta": "recovered"})
        yield _success_sse_event("resp_protocol_ok")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "input": "hi", "stream": True}
    async with async_client.stream("POST", "/v1/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    event_text = json.dumps(events)
    event_types = [event.get("type") for event in events]
    assert call_count == 2
    assert "resp_protocol_failed" not in event_text
    assert "response.failed" not in event_types
    assert "response.output_text.delta" in event_types
    assert event_types[-1] == "response.completed"


@pytest.mark.asyncio
async def test_v1_responses_visible_output_failure_is_not_replayed(async_client, monkeypatch):

    await _import_account(async_client, "acc_public_visible", "public-visible@example.com")

    call_count = 0

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        nonlocal call_count
        call_count += 1
        yield _sse_event(
            {
                "type": "response.created",
                "response": {"id": "resp_visible_failure", "object": "response", "status": "in_progress", "output": []},
            }
        )
        yield _sse_event({"type": "response.output_text.delta", "delta": "partial"})
        yield _stream_incomplete_sse_event("resp_visible_failure")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "input": "hi", "stream": True}
    async with async_client.stream("POST", "/v1/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    event_types = [event.get("type") for event in events]
    deltas = [event.get("delta") for event in events if event.get("type") == "response.output_text.delta"]
    failed = [event for event in events if event.get("type") == "response.failed"]
    assert call_count == 1
    assert deltas == ["partial"]
    assert len(failed) == 1
    assert failed[0].get("response", {}).get("error", {}).get("code") == "stream_incomplete"
    assert "response.completed" not in event_types


@pytest.mark.asyncio
async def test_stream_http_500_after_text_is_surfaced_without_same_account_replay(async_client, monkeypatch):

    await _import_account(async_client, "acc_midtext_500", "midtext500@example.com")

    call_count = 0

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        nonlocal call_count
        call_count += 1
        yield _sse_event({"type": "response.output_text.delta", "delta": "partial answer"})
        raise ProxyResponseError(
            500,
            openai_error("server_error", "late server error"),
            failure_phase="body",
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    deltas = [e for e in events if e.get("type") == "response.output_text.delta"]
    failed = [e for e in events if e.get("type") == "response.failed"]
    completed = [e for e in events if e.get("type") == "response.completed"]
    assert [e.get("delta") for e in deltas] == ["partial answer"]
    assert len(failed) == 1
    assert failed[0].get("response", {}).get("error", {}).get("code") == "server_error"
    assert completed == []
    assert call_count == 1


@pytest.mark.asyncio
async def test_stream_http_429_after_text_is_surfaced_without_account_failover(async_client, monkeypatch):

    await _import_account(async_client, "acc_midtext_429_a", "midtext429a@example.com")
    await _import_account(async_client, "acc_midtext_429_b", "midtext429b@example.com")

    seen_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_account_ids.append(account_id)
        if account_id == "acc_midtext_429_a":
            yield _sse_event({"type": "response.output_text.delta", "delta": "visible"})
            raise ProxyResponseError(
                429,
                openai_error("usage_limit_reached", "usage limit reached"),
                failure_phase="body",
            )
        yield _success_sse_event("resp_should_not_replay")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = _extract_events(lines)
    deltas = [e for e in events if e.get("type") == "response.output_text.delta"]
    failed = [e for e in events if e.get("type") == "response.failed"]
    completed = [e for e in events if e.get("type") == "response.completed"]
    assert [e.get("delta") for e in deltas] == ["visible"]
    assert len(failed) == 1
    assert failed[0].get("response", {}).get("error", {}).get("code") == "usage_limit_reached"
    assert completed == []
    assert seen_account_ids == ["acc_midtext_429_a"]


@pytest.mark.asyncio
async def test_v1_responses_non_streaming_500_preserves_http_status(async_client, monkeypatch):

    await _import_account(async_client, "acc_prop_500", "prop500@example.com")

    call_count = 0

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        nonlocal call_count
        call_count += 1
        raise ProxyResponseError(
            500,
            openai_error("server_error", "An error occurred while processing your request."),
            failure_phase="status",
        )
        yield ""

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "input": "hi"}
    response = await async_client.post("/v1/responses", json=payload)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "server_error"

    assert call_count == 3


@pytest.mark.asyncio
async def test_compact_500_succeeds_on_second_try_same_account(async_client, monkeypatch):

    await _import_account(async_client, "acc_c500_1", "c500@example.com")

    call_count = 0
    seen_account_ids: list[str | None] = []

    async def fake_compact(payload, headers, access_token, account_id):
        nonlocal call_count
        call_count += 1
        seen_account_ids.append(account_id)
        if call_count == 1:
            raise ProxyResponseError(
                500,
                openai_error("server_error", "An error occurred while processing your request."),
                failure_phase="status",
                retryable_same_contract=True,
            )
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200
    assert response.json()["object"] == "response.compaction"

    assert len(seen_account_ids) == 2
    assert seen_account_ids[0] == seen_account_ids[1]


@pytest.mark.asyncio
async def test_compact_500_succeeds_on_third_try(async_client, monkeypatch):

    await _import_account(async_client, "acc_c500_3", "c500_3@example.com")

    call_count = 0
    seen_account_ids: list[str | None] = []

    async def fake_compact(payload, headers, access_token, account_id):
        nonlocal call_count
        call_count += 1
        seen_account_ids.append(account_id)
        if call_count <= 2:
            raise ProxyResponseError(
                500,
                openai_error("server_error", "server error"),
                failure_phase="status",
                retryable_same_contract=True,
            )
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200

    assert len(seen_account_ids) == 3
    assert len(set(seen_account_ids)) == 1


@pytest.mark.asyncio
async def test_compact_500_exhausts_retries_then_failover(async_client, monkeypatch):

    await _import_account(async_client, "acc_cfo_a", "cfo_a@example.com")
    await _import_account(async_client, "acc_cfo_b", "cfo_b@example.com")

    seen_account_ids: list[str | None] = []

    async def fake_compact(payload, headers, access_token, account_id):
        seen_account_ids.append(account_id)
        if account_id == "acc_cfo_a":
            raise ProxyResponseError(
                500,
                openai_error("server_error", "server error"),
                failure_phase="status",
                retryable_same_contract=True,
            )
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200

    a_calls = [aid for aid in seen_account_ids if aid == "acc_cfo_a"]
    b_calls = [aid for aid in seen_account_ids if aid == "acc_cfo_b"]
    assert len(a_calls) == 3
    assert len(b_calls) >= 1


@pytest.mark.asyncio
async def test_compact_quota_exceeded_transparent_failover(async_client, monkeypatch):

    await _import_account(async_client, "acc_compact_quota_a", "compactquotaa@example.com")
    await _import_account(async_client, "acc_compact_quota_b", "compactquotab@example.com")

    seen_account_ids: list[str | None] = []

    async def fake_compact(payload, headers, access_token, account_id):
        seen_account_ids.append(account_id)
        if account_id == "acc_compact_quota_a":
            raise ProxyResponseError(
                429,
                openai_error("quota_exceeded", "quota exceeded"),
                failure_phase="status",
            )
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200
    assert response.json()["object"] == "response.compaction"
    assert seen_account_ids[:2] == ["acc_compact_quota_a", "acc_compact_quota_b"]

    async with SessionLocal() as session:
        account_id = generate_unique_account_id("acc_compact_quota_a", "compactquotaa@example.com")
        account_a = await session.get(Account, account_id)
        assert account_a is not None
        await session.refresh(account_a)
        assert account_a.status == AccountStatus.QUOTA_EXCEEDED


@pytest.mark.asyncio
async def test_compact_500_all_accounts_exhausted(async_client, monkeypatch):

    await _import_account(async_client, "acc_call_a", "call_a@example.com")

    async def fake_compact(payload, headers, access_token, account_id):
        raise ProxyResponseError(
            500,
            openai_error("server_error", "persistent error"),
            failure_phase="status",
            retryable_same_contract=True,
        )

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)

    assert response.status_code in (500, 503)


@pytest.mark.asyncio
async def test_compact_non_500_error_not_retried_as_transient(async_client, monkeypatch):

    await _import_account(async_client, "acc_c400", "c400@example.com")

    call_count = 0

    async def fake_compact(payload, headers, access_token, account_id):
        nonlocal call_count
        call_count += 1
        raise ProxyResponseError(
            400,
            openai_error("invalid_request_error", "bad request"),
            failure_phase="status",
        )

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 400

    assert call_count == 1


@pytest.mark.asyncio
async def test_compact_502_still_uses_safe_retry_budget(async_client, monkeypatch):

    await _import_account(async_client, "acc_c502", "c502@example.com")

    call_count = 0
    seen_account_ids: list[str | None] = []

    async def fake_compact(payload, headers, access_token, account_id):
        nonlocal call_count
        call_count += 1
        seen_account_ids.append(account_id)
        if call_count == 1:
            raise ProxyResponseError(
                502,
                openai_error("upstream_error", "bad gateway"),
                failure_phase="status",
                retryable_same_contract=True,
            )
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200

    assert call_count == 2
    assert seen_account_ids[0] == seen_account_ids[1]


@pytest.mark.asyncio
async def test_compact_sticky_503_unknown_code_excludes_failing_account_on_failover(async_client, monkeypatch):
    await _import_account(async_client, "acc_sticky_503_a", "sticky503a@example.com")
    await _import_account(async_client, "acc_sticky_503_b", "sticky503b@example.com")

    seen_account_ids: list[str | None] = []

    async def fake_compact(payload, headers, access_token, account_id):
        seen_account_ids.append(account_id)
        if account_id == "acc_sticky_503_a":
            raise ProxyResponseError(
                503,
                openai_error("bad_gateway", "Bad gateway"),
                failure_phase="status",
            )
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post(
        "/backend-api/codex/responses/compact",
        json=payload,
        headers={"x-codex-session-id": "sticky-session-503"},
    )
    assert response.status_code == 200
    assert response.json()["object"] == "response.compaction"
    assert seen_account_ids[:2] == ["acc_sticky_503_a", "acc_sticky_503_b"]
