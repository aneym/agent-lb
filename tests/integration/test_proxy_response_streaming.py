from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from starlette.requests import Request

import app.modules.proxy.api as proxy_api_module
import app.modules.proxy.service as proxy_module
from app.core.clients.proxy import ProxyResponseError
from app.core.utils.sse import CODEX_KEEPALIVE_FRAME, SSE_KEEPALIVE_FRAME
from app.db.models import Account, AccountStatus, RequestLog
from app.db.session import SessionLocal
from app.dependencies import ProxyContext
from tests.integration.proxy_api_extended_support import (
    _extract_first_event,
    _import_account,
    _sse_event,
)
from tests.integration.proxy_api_extended_support import (
    _force_usage_weighted_routing as _force_usage_weighted_routing,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_proxy_stream_records_cached_and_reasoning_tokens(async_client, monkeypatch):
    expected_account_id = await _import_account(async_client, "acc_usage", "usage@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        usage = {
            "input_tokens": 10,
            "output_tokens": 5,
            "input_tokens_details": {"cached_tokens": 3},
            "output_tokens_details": {"reasoning_tokens": 2},
        }
        event = {"type": "response.completed", "response": {"id": "resp_1", "usage": usage}}
        yield _sse_event(event)

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    request_id = "req_usage_123"
    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json=payload,
        headers={"x-request-id": request_id},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.completed"

    async with SessionLocal() as session:
        result = await session.execute(
            select(RequestLog)
            .where(RequestLog.account_id == expected_account_id)
            .order_by(RequestLog.requested_at.desc())
        )
        log = result.scalars().first()
        assert log is not None
        assert log.request_id == "resp_1"
        assert log.input_tokens == 10
        assert log.output_tokens == 5
        assert log.cached_input_tokens == 3
        assert log.reasoning_tokens == 2
        assert log.status == "success"


@pytest.mark.asyncio
async def test_stream_responses_starts_sse_keepalive_before_first_upstream_event(monkeypatch):
    upstream_started = asyncio.Event()
    release_upstream = asyncio.Event()

    class _FakeService:
        async def rate_limit_headers(self):
            return {}

        async def stream_responses(self, *args, **kwargs):
            del args, kwargs
            upstream_started.set()
            await release_upstream.wait()
            event = {"type": "response.completed", "response": {"id": "resp_delayed"}}
            yield _sse_event(event)

    settings = SimpleNamespace(
        http_responses_session_bridge_enabled=False,
        sse_keepalive_interval_seconds=0.01,
    )
    monkeypatch.setattr(proxy_api_module, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_api_module.proxy_service_module, "get_settings", lambda: settings)

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/backend-api/codex/responses",
            "headers": [],
        }
    )
    payload = proxy_api_module.ResponsesRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    )

    response = await proxy_api_module._stream_responses(
        request,
        payload,
        ProxyContext(service=cast(proxy_module.ProxyService, _FakeService())),
        api_key=None,
    )

    assert isinstance(response, StreamingResponse)
    assert upstream_started.is_set() is True
    iterator = response.body_iterator.__aiter__()
    first_chunk = await asyncio.wait_for(iterator.__anext__(), timeout=0.2)
    assert first_chunk == SSE_KEEPALIVE_FRAME
    release_upstream.set()
    chunks = [cast(str, await asyncio.wait_for(iterator.__anext__(), timeout=0.2)) for _ in range(2)]
    assert any("response.completed" in chunk for chunk in chunks)


@pytest.mark.asyncio
async def test_codex_route_stream_responses_starts_event_keepalive_before_first_upstream_event(monkeypatch):
    upstream_started = asyncio.Event()
    release_upstream = asyncio.Event()

    class _FakeService:
        async def rate_limit_headers(self):
            return {}

        async def stream_responses(self, *args, **kwargs):
            del args, kwargs
            upstream_started.set()
            await release_upstream.wait()
            event = {"type": "response.completed", "response": {"id": "resp_delayed"}}
            yield _sse_event(event)

    settings = SimpleNamespace(
        http_responses_session_bridge_enabled=False,
        sse_keepalive_interval_seconds=0.01,
    )
    monkeypatch.setattr(proxy_api_module, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_api_module.proxy_service_module, "get_settings", lambda: settings)

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/backend-api/codex/responses",
            "headers": [],
        }
    )
    payload = proxy_api_module.ResponsesRequest.model_validate(
        {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    )

    response = await proxy_api_module._stream_responses(
        request,
        payload,
        ProxyContext(service=cast(proxy_module.ProxyService, _FakeService())),
        api_key=None,
        enforce_openai_sdk_contract=False,
    )

    assert isinstance(response, StreamingResponse)
    assert upstream_started.is_set() is True
    iterator = response.body_iterator.__aiter__()
    first_chunk = await asyncio.wait_for(iterator.__anext__(), timeout=0.2)
    assert first_chunk == CODEX_KEEPALIVE_FRAME
    release_upstream.set()
    chunks = [cast(str, await asyncio.wait_for(iterator.__anext__(), timeout=0.2)) for _ in range(2)]
    assert any("response.completed" in chunk for chunk in chunks)


@pytest.mark.asyncio
async def test_proxy_stream_retries_rate_limit_then_success(async_client, monkeypatch):
    expected_account_id_1 = await _import_account(async_client, "acc_1", "one@example.com")
    expected_account_id_2 = await _import_account(async_client, "acc_2", "two@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        if account_id == "acc_1":
            event = {
                "type": "response.failed",
                "response": {"error": {"code": "rate_limit_exceeded", "message": "slow down"}},
            }
            yield _sse_event(event)
            return
        event = {
            "type": "response.completed",
            "response": {"id": "resp_2", "usage": {"input_tokens": 1, "output_tokens": 1}},
        }
        yield _sse_event(event)

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json=payload,
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.completed"

    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog).order_by(RequestLog.requested_at.desc()))
        logs = list(result.scalars().all())
        assert len(logs) == 2
        by_account = {log.account_id: log for log in logs}
        assert by_account[expected_account_id_1].status == "error"
        assert by_account[expected_account_id_1].error_code == "rate_limit_exceeded"
        assert by_account[expected_account_id_1].error_message == "slow down"
        assert by_account[expected_account_id_2].status == "success"

    async with SessionLocal() as session:
        acc1 = await session.get(Account, expected_account_id_1)
        acc2 = await session.get(Account, expected_account_id_2)
        assert acc1 is not None
        assert acc2 is not None
        assert acc1.status == AccountStatus.RATE_LIMITED
        assert acc2.status == AccountStatus.ACTIVE


@pytest.mark.asyncio
async def test_proxy_stream_fails_over_after_first_event_stream_idle_timeout(async_client, monkeypatch):
    expected_account_id_1 = await _import_account(async_client, "acc_idle_1", "idle-one@example.com")
    expected_account_id_2 = await _import_account(async_client, "acc_idle_2", "idle-two@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        if account_id == "acc_idle_1":
            event = {
                "type": "response.failed",
                "response": {"error": {"code": "stream_idle_timeout", "message": "idle"}},
            }
            yield _sse_event(event)
            return
        event = {"type": "response.completed", "response": {"id": "resp_idle_ok", "usage": {}}}
        yield _sse_event(event)

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json=payload,
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.completed"
    assert event["response"]["id"] == "resp_idle_ok"

    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog).order_by(RequestLog.requested_at.desc()))
        logs = list(result.scalars().all())
        assert len(logs) == 2
        by_account = {log.account_id: log for log in logs}
        assert by_account[expected_account_id_1].error_code == "stream_idle_timeout"
        assert by_account[expected_account_id_2].status == "success"


@pytest.mark.asyncio
async def test_proxy_stream_drops_forwarded_headers(async_client, monkeypatch):
    await _import_account(async_client, "acc_headers", "headers@example.com")
    captured_headers: dict[str, str] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        captured_headers.update(headers)
        event = {
            "type": "response.completed",
            "response": {"id": "resp_headers", "usage": {"input_tokens": 1, "output_tokens": 1}},
        }
        yield _sse_event(event)

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    request_headers = {
        "x-forwarded-for": "1.2.3.4",
        "x-forwarded-proto": "https",
        "x-real-ip": "1.2.3.4",
        "forwarded": "for=1.2.3.4;proto=https",
        "cf-connecting-ip": "1.2.3.4",
        "cf-ray": "ray123",
        "true-client-ip": "1.2.3.4",
        "user-agent": "codex-test",
    }
    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json=payload,
        headers=request_headers,
    ) as resp:
        assert resp.status_code == 200
        _ = [line async for line in resp.aiter_lines() if line]

    normalized = {key.lower() for key in captured_headers}
    assert "x-forwarded-for" not in normalized
    assert "x-forwarded-proto" not in normalized
    assert "x-real-ip" not in normalized
    assert "forwarded" not in normalized
    assert "cf-connecting-ip" not in normalized
    assert "cf-ray" not in normalized
    assert "true-client-ip" not in normalized
    assert "user-agent" in normalized


@pytest.mark.asyncio
async def test_proxy_stream_usage_limit_returns_http_error(async_client, monkeypatch):
    raw_account_id = "acc_stream_usage_limit"
    expected_account_id = await _import_account(async_client, raw_account_id, "stream-usage-limit@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        assert account_id == raw_account_id
        raise ProxyResponseError(
            429,
            {
                "error": {
                    "type": "usage_limit_reached",
                    "message": "The usage limit has been reached",
                    "plan_type": "plus",
                    "resets_at": 1767612327,
                }
            },
        )
        if False:
            yield ""

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    monkeypatch.setattr(proxy_module, "_STREAM_MAX_ACCOUNT_ATTEMPTS", 1)
    monkeypatch.setattr(proxy_api_module, "_STREAM_STARTUP_ERROR_PROBE_SECONDS", 30.0)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    response = await async_client.post("/backend-api/codex/responses", json=payload)
    assert response.status_code == 429
    error = response.json()["error"]
    assert error["type"] == "usage_limit_reached"
    assert error["plan_type"] == "plus"
    assert error["resets_at"] == 1767612327

    async with SessionLocal() as session:
        acc = await session.get(Account, expected_account_id)
        assert acc is not None
        assert acc.status == AccountStatus.RATE_LIMITED
