from __future__ import annotations
import json
from collections.abc import AsyncGenerator
from typing import Any, cast
import pytest
import app.modules.proxy.service as proxy_module
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.http_responses_bridge_streams import (
    _CreatedOnlyUpstreamWebSocket,
    _FailingSendThenCloseUpstreamWebSocket,
)
from tests.integration.test_http_responses_bridge import (
    _FakeBridgeUpstreamWebSocket,
    _FakeUpstreamMessage,
    _cleanup_http_bridge_sessions,
    _get_account,
    _import_account,
    _install_bridge_settings,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_send_retry_keeps_session_open_for_followup_request(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_send_retry_followup",
        "http-bridge-send-retry-followup@example.com",
    )
    account = await _get_account(account_id)
    upstreams = [_FailingSendThenCloseUpstreamWebSocket(), _FakeBridgeUpstreamWebSocket()]
    connect_count = 0
    service = get_proxy_service_for_app(app_instance)

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        request_stage="first_turn",
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
        api_key=None,
        preferred_account_id=None,
    ):
        del preferred_account_id
        del (
            self,
            deadline,
            request_id,
            kind,
            request_stage,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        nonlocal connect_count
        upstream = upstreams[min(connect_count, len(upstreams) - 1)]
        connect_count += 1
        if isinstance(upstream, _FakeBridgeUpstreamWebSocket) and not upstream._messages.qsize():
            await upstream._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.created",
                            "response": {
                                "id": "resp_retry_send_followup",
                                "object": "response",
                                "status": "in_progress",
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            )
            await upstream._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.completed",
                            "response": {
                                "id": "resp_retry_send_followup",
                                "object": "response",
                                "status": "completed",
                                "usage": {
                                    "input_tokens": 24,
                                    "output_tokens": 2,
                                    "total_tokens": 26,
                                    "input_tokens_details": {"cached_tokens": 20},
                                    "output_tokens_details": {"reasoning_tokens": 0},
                                },
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            )
        return upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    payload = {
        "model": "gpt-5.1",
        "instructions": "Return exactly OK.",
        "input": "retry-send-followup",
        "prompt_cache_key": "retry-send-followup-key",
    }
    first = await async_client.post("/v1/responses", json=payload)
    second = await async_client.post("/v1/responses", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert connect_count == 2

    session_key = proxy_module._HTTPBridgeSessionKey(
        affinity_kind="prompt_cache",
        affinity_key="retry-send-followup-key",
        api_key_id=None,
    )
    async with service._http_bridge_lock:
        session = service._http_bridge_sessions[session_key]
        assert session.closed is False


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_stream_cancel_retires_session(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_stream_cancel",
        "http-bridge-stream-cancel@example.com",
    )
    service = get_proxy_service_for_app(app_instance)
    account = await _get_account(account_id)
    fake_upstream = _CreatedOnlyUpstreamWebSocket()

    async def fake_select_account_with_budget(
        self,
        deadline,
        *,
        request_id,
        kind,
        request_stage="first_turn",
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset_accounts,
        routing_strategy,
        model,
        exclude_account_ids=None,
        additional_limit_name=None,
        api_key=None,
        preferred_account_id=None,
    ):
        del preferred_account_id
        del (
            self,
            deadline,
            request_id,
            kind,
            request_stage,
            sticky_key,
            sticky_kind,
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        return fake_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    payload = proxy_module.ResponsesRequest(
        model="gpt-5.1",
        instructions="Return exactly OK.",
        input="cancel-stream",
        prompt_cache_key="cancel-stream-key",
    )
    stream = service._stream_via_http_bridge(
        payload,
        {},
        codex_session_affinity=False,
        propagate_http_errors=False,
        openai_cache_affinity=True,
        api_key=None,
        api_key_reservation=None,
        suppress_text_done_events=False,
        idle_ttl_seconds=120.0,
        codex_idle_ttl_seconds=900.0,
        max_sessions=128,
        queue_limit=8,
    )
    stream = cast(AsyncGenerator[str, None], stream)

    first_event = await stream.__anext__()
    assert "response.created" in first_event
    await stream.aclose()

    session_key = proxy_module._HTTPBridgeSessionKey(
        affinity_kind="prompt_cache",
        affinity_key="cancel-stream-key",
        api_key_id=None,
    )
    async with service._http_bridge_lock:
        session = service._http_bridge_sessions[session_key]
    async with session.pending_lock:
        assert not session.pending_requests
        assert session.queued_request_count == 0
    assert session.closed is True
    assert session.upstream_control.retire_after_drain is True
    assert fake_upstream.closed is True
