from __future__ import annotations

import json

import pytest

import app.modules.proxy.service as proxy_module
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from tests.integration.proxy_transient_retry_support import (
    _extract_events,
    _import_account,
    _sse_event,
    _stream_incomplete_sse_event,
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
