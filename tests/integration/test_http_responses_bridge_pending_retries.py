from __future__ import annotations
import asyncio
import json
import time
from collections import deque
from types import SimpleNamespace
from typing import Any, cast
import anyio
import pytest
import app.modules.proxy.service as proxy_module
from app.db.models import Account, AccountStatus, DashboardSettings, RequestLog
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.http_responses_bridge_streams import (
    _FailingSendThenCloseUpstreamWebSocket,
    _RecordingUpstreamWebSocket,
    _SilentUpstreamWebSocket,
)
from tests.integration.test_http_responses_bridge import (
    _FakeBridgeUpstreamWebSocket,
    _FakeUpstreamMessage,
    _cleanup_http_bridge_sessions,
    _get_account,
    _import_account,
    _install_bridge_settings,
    _wait_for_event,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_cancellation_releases_queued_slot(async_client, app_instance, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(async_client, "acc_http_bridge_cancel", "http-bridge-cancel@example.com")
    service = get_proxy_service_for_app(app_instance)
    account = await _get_account(account_id)
    upstream = _SilentUpstreamWebSocket()

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
        return upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    payload = proxy_module.ResponsesRequest(
        model="gpt-5.1",
        instructions="Return exactly OK.",
        input="cancel-me",
        prompt_cache_key="cancel-key",
    )
    affinity = proxy_module._sticky_key_for_responses_request(
        payload,
        {},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    key = proxy_module._make_http_bridge_session_key(
        payload,
        headers={},
        affinity=affinity,
        api_key=None,
        request_id="req_cancel",
    )
    session = await service._get_or_create_http_bridge_session(
        key,
        headers={},
        affinity=affinity,
        api_key=None,
        request_model="gpt-5.1",
        idle_ttl_seconds=120.0,
        max_sessions=128,
    )

    await session.response_create_gate.acquire()
    request_state, text_data = service._prepare_http_bridge_request(payload, {}, api_key=None, api_key_reservation=None)
    request_state.transport = "http"
    task = asyncio.create_task(
        service._submit_http_bridge_request(
            session,
            request_state=request_state,
            text_data=text_data,
            queue_limit=8,
        )
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert session.queued_request_count == 0
    async with session.pending_lock:
        assert list(session.pending_requests) == []
    session.response_create_gate.release()
    await service._close_http_bridge_session(session)


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_send_retry_restarts_reader(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_send_retry",
        "http-bridge-send-retry@example.com",
    )
    account = await _get_account(account_id)
    upstreams = [_FailingSendThenCloseUpstreamWebSocket(), _FakeBridgeUpstreamWebSocket()]
    connect_count = 0

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
        upstream = upstreams[connect_count]
        connect_count += 1
        if isinstance(upstream, _FakeBridgeUpstreamWebSocket) and not upstream._messages.qsize():
            await upstream._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.created",
                            "response": {"id": "resp_retry_send", "object": "response", "status": "in_progress"},
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
                                "id": "resp_retry_send",
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

    response = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "retry-send",
            "prompt_cache_key": "retry-send-key",
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] == "resp_retry_send"
    assert connect_count == 2


@pytest.mark.asyncio
async def test_retry_http_bridge_precreated_request_releases_pending_lock_before_reconnect(app_instance, monkeypatch):
    service = get_proxy_service_for_app(app_instance)
    session = proxy_module._HTTPBridgeSession(
        key=proxy_module._HTTPBridgeSessionKey("prompt_cache", "retry-lock-key", None),
        headers={},
        affinity=proxy_module._AffinityPolicy(
            key="retry-lock-key",
            kind=proxy_module.StickySessionKind.PROMPT_CACHE,
            max_age_seconds=300,
        ),
        request_model="gpt-5.1",
        account=cast(Account, SimpleNamespace(id="acct-retry", status=AccountStatus.ACTIVE)),
        upstream=cast(proxy_module.UpstreamResponsesWebSocket, _SilentUpstreamWebSocket()),
        upstream_control=proxy_module._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=1,
        last_used_at=time.monotonic(),
        idle_ttl_seconds=120.0,
    )
    request_state = proxy_module._WebSocketRequestState(
        request_id="req-precreated-retry",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        awaiting_response_created=True,
        response_create_gate_acquired=True,
        request_text=json.dumps({"type": "response.create", "model": "gpt-5.1", "input": []}),
    )
    session.pending_requests.append(request_state)
    reconnect_started = asyncio.Event()
    allow_reconnect_finish = asyncio.Event()
    lock_reacquired = asyncio.Event()
    replacement_upstream = _RecordingUpstreamWebSocket()

    async def fake_reconnect(self, target_session, *, request_state, restart_reader=False):
        del self, request_state, restart_reader
        reconnect_started.set()
        await _wait_for_event(allow_reconnect_finish)
        target_session.upstream = replacement_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_reconnect_http_bridge_session", fake_reconnect)

    retry_task = asyncio.create_task(service._retry_http_bridge_precreated_request(session))
    await _wait_for_event(reconnect_started)

    async def acquire_pending_lock() -> None:
        async with session.pending_lock:
            lock_reacquired.set()

    lock_task = asyncio.create_task(acquire_pending_lock())
    await asyncio.wait_for(lock_reacquired.wait(), timeout=1.0)
    allow_reconnect_finish.set()

    assert await retry_task is True
    await lock_task
    assert replacement_upstream.sent_text == [request_state.request_text]


@pytest.mark.asyncio
async def test_retry_http_bridge_precreated_request_ignores_existing_response_id_entries(app_instance, monkeypatch):
    service = get_proxy_service_for_app(app_instance)
    session = proxy_module._HTTPBridgeSession(
        key=proxy_module._HTTPBridgeSessionKey("prompt_cache", "retry-race-key", None),
        headers={},
        affinity=proxy_module._AffinityPolicy(
            key="retry-race-key",
            kind=proxy_module.StickySessionKind.PROMPT_CACHE,
            max_age_seconds=300,
        ),
        request_model="gpt-5.1",
        account=cast(Account, SimpleNamespace(id="acct-race", status=AccountStatus.ACTIVE)),
        upstream=cast(proxy_module.UpstreamResponsesWebSocket, _SilentUpstreamWebSocket()),
        upstream_control=proxy_module._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=2,
        last_used_at=time.monotonic(),
        idle_ttl_seconds=120.0,
    )
    existing_request = proxy_module._WebSocketRequestState(
        request_id="req-existing",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        response_id="resp-existing",
        awaiting_response_created=False,
    )
    retry_request = proxy_module._WebSocketRequestState(
        request_id="req-precreated-race",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        awaiting_response_created=True,
        request_text=json.dumps({"type": "response.create", "model": "gpt-5.1", "input": ["retry"]}),
    )
    session.pending_requests.extend([existing_request, retry_request])
    replacement_upstream = _RecordingUpstreamWebSocket()

    async def fake_reconnect(self, target_session, *, request_state, restart_reader=False):
        del self, request_state, restart_reader
        target_session.upstream = replacement_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_reconnect_http_bridge_session", fake_reconnect)

    assert await service._retry_http_bridge_precreated_request(session) is True
    assert replacement_upstream.sent_text == [retry_request.request_text]
