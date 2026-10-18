from __future__ import annotations
import asyncio
import contextlib
import json
import time
import pytest
import app.modules.proxy.service as proxy_module
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.test_http_responses_bridge import (
    _FakeBridgeUpstreamWebSocket,
    _SettingsCache,
    _cleanup_http_bridge_sessions,
    _get_account,
    _import_account,
    _install_bridge_settings_with_limits,
    _make_dashboard_settings,
    _wait_for_event,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_reconnect_fails_when_reader_cancel_times_out(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_reconnect_cancel_timeout",
        "http-bridge-reconnect-cancel-timeout@example.com",
    )
    account = await _get_account(account_id)
    service = get_proxy_service_for_app(app_instance)
    upstreams = [_FakeBridgeUpstreamWebSocket(), _FakeBridgeUpstreamWebSocket()]

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
        return upstreams.pop(0)

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    payload = proxy_module.ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )
    affinity = proxy_module._sticky_key_for_responses_request(
        payload,
        {"x-codex-turn-state": "timeout_turn_state"},
        codex_session_affinity=True,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    key = proxy_module._make_http_bridge_session_key(
        payload,
        headers={"x-codex-turn-state": "timeout_turn_state"},
        affinity=affinity,
        api_key=None,
        request_id="req_timeout_turn_state",
    )
    bridge_session = await service._get_or_create_http_bridge_session(
        key,
        headers={"x-codex-turn-state": "timeout_turn_state"},
        affinity=affinity,
        api_key=None,
        request_model=payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=8,
    )
    original_upstream = bridge_session.upstream

    blocker = asyncio.Event()

    async def blocking_reader_task() -> None:
        await _wait_for_event(blocker)

    blocking_reader = asyncio.create_task(blocking_reader_task())
    bridge_session.upstream_reader = blocking_reader

    async def fake_await_cancelled_task(task, *, timeout_seconds=1.0, label):
        del task, timeout_seconds, label
        return False

    monkeypatch.setattr(proxy_module, "_await_cancelled_task", fake_await_cancelled_task)

    request_state = proxy_module._WebSocketRequestState(
        request_id="req-timeout-reconnect",
        model=payload.model,
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        awaiting_response_created=True,
        response_create_gate_acquired=True,
        request_text=json.dumps({"type": "response.create", "model": "gpt-5.4", "input": []}),
    )

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service._reconnect_http_bridge_session(
            bridge_session,
            request_state=request_state,
            restart_reader=True,
        )

    error_payload = exc_info.value.payload["error"]
    assert exc_info.value.status_code == 502
    assert error_payload.get("code") == "upstream_unavailable"
    assert "reader did not shut down cleanly" in (error_payload.get("message") or "")
    assert bridge_session.closed is True
    assert bridge_session.upstream is original_upstream
    blocking_reader.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await blocking_reader


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_prefers_evicting_prompt_cache_session_before_codex_session(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(monkeypatch, enabled=True, max_sessions=2, codex_idle_ttl_seconds=600.0)
    account_id = await _import_account(async_client, "acc_http_bridge_evict_pref", "http-bridge-evict-pref@example.com")
    account = await _get_account(account_id)
    service = get_proxy_service_for_app(app_instance)
    upstreams = [_FakeBridgeUpstreamWebSocket(), _FakeBridgeUpstreamWebSocket(), _FakeBridgeUpstreamWebSocket()]

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
        return upstreams.pop(0)

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    payload = proxy_module.ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )

    codex_affinity = proxy_module._sticky_key_for_responses_request(
        payload,
        {"x-codex-turn-state": "turn_state_1"},
        codex_session_affinity=True,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    codex_key = proxy_module._make_http_bridge_session_key(
        payload,
        headers={"x-codex-turn-state": "turn_state_1"},
        affinity=codex_affinity,
        api_key=None,
        request_id="req_codex",
    )
    codex_session = await service._get_or_create_http_bridge_session(
        codex_key,
        headers={"x-codex-turn-state": "turn_state_1"},
        affinity=codex_affinity,
        api_key=None,
        request_model=payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=2,
    )
    codex_session.last_used_at = time.monotonic() - 50.0

    prompt_payload = proxy_module.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [{"role": "user", "content": "hi"}],
            "prompt_cache_key": "prompt_cache_1",
        }
    )
    prompt_affinity = proxy_module._sticky_key_for_responses_request(
        prompt_payload,
        {},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    prompt_key = proxy_module._make_http_bridge_session_key(
        prompt_payload,
        headers={},
        affinity=prompt_affinity,
        api_key=None,
        request_id="req_prompt",
    )
    prompt_session = await service._get_or_create_http_bridge_session(
        prompt_key,
        headers={},
        affinity=prompt_affinity,
        api_key=None,
        request_model=prompt_payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=2,
    )
    prompt_session.last_used_at = time.monotonic() - 5.0

    next_payload = proxy_module.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "next",
            "input": [{"role": "user", "content": "next"}],
            "prompt_cache_key": "prompt_cache_2",
        }
    )
    next_affinity = proxy_module._sticky_key_for_responses_request(
        next_payload,
        {},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    next_key = proxy_module._make_http_bridge_session_key(
        next_payload,
        headers={},
        affinity=next_affinity,
        api_key=None,
        request_id="req_prompt_2",
    )

    created = await service._get_or_create_http_bridge_session(
        next_key,
        headers={},
        affinity=next_affinity,
        api_key=None,
        request_model=next_payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=2,
    )

    async with service._http_bridge_lock:
        assert codex_key in service._http_bridge_sessions
        assert prompt_key not in service._http_bridge_sessions
        assert next_key in service._http_bridge_sessions
    assert created.key == next_key


@pytest.mark.asyncio
async def test_get_or_create_http_bridge_session_honors_passed_prompt_cache_idle_ttl(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(
        monkeypatch,
        enabled=True,
        prompt_cache_idle_ttl_seconds=1800.0,
    )
    account_id = await _import_account(async_client, "acc_prompt_ttl", "prompt-ttl@example.com")
    account = await _get_account(account_id)
    service = get_proxy_service_for_app(app_instance)
    fake_upstream = _FakeBridgeUpstreamWebSocket()
    payload = proxy_module.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [{"role": "user", "content": "hi"}],
            "prompt_cache_key": "prompt-cache-ttl-test",
        }
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
        request_id="req_prompt_ttl",
    )
    cached_settings = await proxy_module.get_settings_cache().get()
    monkeypatch.setattr(
        proxy_module,
        "get_settings_cache",
        lambda: _SettingsCache(
            _make_dashboard_settings(
                prefer_earlier_reset_accounts=cached_settings.prefer_earlier_reset_accounts,
                gateway_safe_mode=cached_settings.http_responses_session_bridge_gateway_safe_mode,
                prompt_cache_idle_ttl_seconds=3600,
            )
        ),
    )

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

    async def fake_open_upstream_websocket_with_budget(self, account, headers, *, timeout_seconds):
        del self, account, headers, timeout_seconds
        return fake_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(
        proxy_module.ProxyService,
        "_open_upstream_websocket_with_budget",
        fake_open_upstream_websocket_with_budget,
    )

    session = await service._get_or_create_http_bridge_session(
        key,
        headers={},
        affinity=affinity,
        api_key=None,
        request_model=payload.model,
        idle_ttl_seconds=proxy_module._effective_http_bridge_idle_ttl_seconds(
            affinity=affinity,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=900.0,
            prompt_cache_idle_ttl_seconds=1800.0,
        ),
        max_sessions=32,
    )

    assert session.idle_ttl_seconds == 1800.0
    await service._close_http_bridge_session(session)
