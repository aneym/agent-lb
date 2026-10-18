from __future__ import annotations
import asyncio
import time
import pytest
import app.modules.proxy.service as proxy_module
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.http_responses_bridge_streams import (
    _CreatedOnlyUpstreamWebSocket,
    _SilentUpstreamWebSocket,
)
from tests.integration.test_http_responses_bridge import (
    _cleanup_http_bridge_sessions,
    _get_account,
    _import_account,
    _install_bridge_settings_with_limits,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_does_not_evict_active_session_when_pool_is_full(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(monkeypatch, enabled=True, max_sessions=1)
    account_id = await _import_account(async_client, "acc_http_bridge_capacity", "http-bridge-capacity@example.com")
    service = get_proxy_service_for_app(app_instance)
    account = await _get_account(account_id)
    hanging_upstream = _CreatedOnlyUpstreamWebSocket()

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
        return hanging_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)
    first_payload = proxy_module.ResponsesRequest(
        model="gpt-5.1",
        instructions="Return exactly OK.",
        input="hold-open",
        prompt_cache_key="active-session-a",
    )
    first_affinity = proxy_module._sticky_key_for_responses_request(
        first_payload,
        {},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    first_key = proxy_module._make_http_bridge_session_key(
        first_payload,
        headers={},
        affinity=first_affinity,
        api_key=None,
        request_id="req_a",
    )
    first_session = await service._get_or_create_http_bridge_session(
        first_key,
        headers={},
        affinity=first_affinity,
        api_key=None,
        request_model="gpt-5.1",
        idle_ttl_seconds=120.0,
        max_sessions=1,
        response_create_concurrency=1,
    )
    async with first_session.pending_lock:
        first_session.pending_requests.append(
            proxy_module._WebSocketRequestState(
                request_id="req-active",
                model="gpt-5.1",
                service_tier=None,
                reasoning_effort=None,
                api_key_reservation=None,
                started_at=time.monotonic(),
                awaiting_response_created=True,
                response_create_gate_acquired=True,
                event_queue=asyncio.Queue(),
                transport="http",
            )
        )
    second_payload = proxy_module.ResponsesRequest(
        model="gpt-5.1",
        instructions="Return exactly OK.",
        input="new-session",
        prompt_cache_key="active-session-b",
    )
    second_affinity = proxy_module._sticky_key_for_responses_request(
        second_payload,
        {},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    second_key = proxy_module._make_http_bridge_session_key(
        second_payload,
        headers={},
        affinity=second_affinity,
        api_key=None,
        request_id="req_b",
    )
    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service._get_or_create_http_bridge_session(
            second_key,
            headers={},
            affinity=second_affinity,
            api_key=None,
            request_model="gpt-5.1",
            idle_ttl_seconds=120.0,
            max_sessions=1,
        )
    exc = exc_info.value
    assert exc.status_code == 429
    assert hanging_upstream.closed is False
    await service._close_http_bridge_session(first_session)


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_does_not_evict_queued_session_when_pool_is_full(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(
        monkeypatch,
        enabled=True,
        max_sessions=1,
        admission_wait_timeout_seconds=30.0,
    )
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_queued_capacity",
        "http-bridge-queued@example.com",
    )
    service = get_proxy_service_for_app(app_instance)
    account = await _get_account(account_id)
    hanging_upstream = _SilentUpstreamWebSocket()

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
        return hanging_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    first_payload = proxy_module.ResponsesRequest(
        model="gpt-5.1",
        instructions="Return exactly OK.",
        input="queued-session",
        prompt_cache_key="queued-session-a",
    )
    first_affinity = proxy_module._sticky_key_for_responses_request(
        first_payload,
        {},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    first_key = proxy_module._make_http_bridge_session_key(
        first_payload,
        headers={},
        affinity=first_affinity,
        api_key=None,
        request_id="req_queue_a",
    )
    first_session = await service._get_or_create_http_bridge_session(
        first_key,
        headers={},
        affinity=first_affinity,
        api_key=None,
        request_model="gpt-5.1",
        idle_ttl_seconds=120.0,
        max_sessions=1,
        response_create_concurrency=1,
    )

    await first_session.response_create_gate.acquire()
    request_state, text_data = service._prepare_http_bridge_request(
        first_payload,
        {},
        api_key=None,
        api_key_reservation=None,
    )
    request_state.transport = "http"
    submit_task = asyncio.create_task(
        service._submit_http_bridge_request(
            first_session,
            request_state=request_state,
            text_data=text_data,
            queue_limit=8,
        )
    )
    await asyncio.sleep(0)

    assert await service._http_bridge_pending_count(first_session) == 1
    async with first_session.pending_lock:
        assert list(first_session.pending_requests) == []
        assert first_session.queued_request_count == 1

    second_payload = proxy_module.ResponsesRequest(
        model="gpt-5.1",
        instructions="Return exactly OK.",
        input="new-session",
        prompt_cache_key="queued-session-b",
    )
    second_affinity = proxy_module._sticky_key_for_responses_request(
        second_payload,
        {},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    second_key = proxy_module._make_http_bridge_session_key(
        second_payload,
        headers={},
        affinity=second_affinity,
        api_key=None,
        request_id="req_queue_b",
    )
    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service._get_or_create_http_bridge_session(
            second_key,
            headers={},
            affinity=second_affinity,
            api_key=None,
            request_model="gpt-5.1",
            idle_ttl_seconds=120.0,
            max_sessions=1,
        )

    exc = exc_info.value
    assert exc.status_code == 429
    assert hanging_upstream.closed is False

    submit_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await submit_task
    first_session.response_create_gate.release()
    await service._close_http_bridge_session(first_session)


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_enforces_queue_limit_atomically_for_same_session(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(monkeypatch, enabled=True, queue_limit=1)
    account_id = await _import_account(async_client, "acc_http_bridge_queue", "http-bridge-queue@example.com")
    service = get_proxy_service_for_app(app_instance)
    account = await _get_account(account_id)
    hanging_upstream = _SilentUpstreamWebSocket()

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
        return hanging_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    payload = proxy_module.ResponsesRequest(
        model="gpt-5.1",
        instructions="Return exactly OK.",
        input="same-session",
        prompt_cache_key="same-session-key",
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
        request_id="req_queue",
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

    first_state, first_text = service._prepare_http_bridge_request(payload, {}, api_key=None, api_key_reservation=None)
    first_state.transport = "http"
    await service._submit_http_bridge_request(session, request_state=first_state, text_data=first_text, queue_limit=1)

    second_state, second_text = service._prepare_http_bridge_request(
        payload, {}, api_key=None, api_key_reservation=None
    )
    second_state.transport = "http"
    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service._submit_http_bridge_request(
            session,
            request_state=second_state,
            text_data=second_text,
            queue_limit=1,
        )

    exc = exc_info.value
    assert exc.status_code == 429
    assert session.queued_request_count == 1
    await service._close_http_bridge_session(session)
