from __future__ import annotations
import json
import time
import pytest
import app.modules.proxy.service as proxy_module
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy._service.http_bridge.mixin import _HTTP_BRIDGE_IDLE_PRUNE_MIN_INTERVAL_SECONDS
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.http_responses_bridge_streams import (
    _PrewarmingBridgeUpstreamWebSocket,
)
from tests.integration.test_http_responses_bridge import (
    _FakeBridgeUpstreamWebSocket,
    _cleanup_http_bridge_sessions,
    _get_account,
    _import_account,
    _install_bridge_settings_with_limits,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_codex_session_uses_extended_idle_ttl(async_client, app_instance, monkeypatch):
    _install_bridge_settings_with_limits(monkeypatch, enabled=True, codex_idle_ttl_seconds=600.0)
    account_id = await _import_account(async_client, "acc_http_bridge_codex_ttl", "http-bridge-codex-ttl@example.com")
    account = await _get_account(account_id)
    service = get_proxy_service_for_app(app_instance)
    fake_upstream = _FakeBridgeUpstreamWebSocket()

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

    payload = proxy_module.ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )
    affinity = proxy_module._sticky_key_for_responses_request(
        payload,
        {"x-codex-turn-state": "turn_state_1"},
        codex_session_affinity=True,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    key = proxy_module._make_http_bridge_session_key(
        payload,
        headers={"x-codex-turn-state": "turn_state_1"},
        affinity=affinity,
        api_key=None,
        request_id="req_1",
    )

    session = await service._get_or_create_http_bridge_session(
        key,
        headers={"x-codex-turn-state": "turn_state_1"},
        affinity=affinity,
        api_key=None,
        request_model=payload.model,
        idle_ttl_seconds=proxy_module._effective_http_bridge_idle_ttl_seconds(
            affinity=affinity,
            idle_ttl_seconds=120.0,
            codex_idle_ttl_seconds=600.0,
        ),
        max_sessions=8,
    )

    session.last_used_at = time.monotonic() - 300.0
    service._http_bridge_last_idle_prune_monotonic -= _HTTP_BRIDGE_IDLE_PRUNE_MIN_INTERVAL_SECONDS + 1.0
    async with service._http_bridge_lock:
        service._prune_http_bridge_sessions_locked()
        assert key in service._http_bridge_sessions

    session.last_used_at = time.monotonic() - 601.0
    service._http_bridge_last_idle_prune_monotonic -= _HTTP_BRIDGE_IDLE_PRUNE_MIN_INTERVAL_SECONDS + 1.0
    async with service._http_bridge_lock:
        service._prune_http_bridge_sessions_locked()
        assert key not in service._http_bridge_sessions


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_creation_honors_prefer_earlier_reset(async_client, app_instance, monkeypatch):
    _install_bridge_settings_with_limits(monkeypatch, enabled=True, prefer_earlier_reset_accounts=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_prefer_earlier_reset",
        "http-bridge-prefer-earlier-reset@example.com",
    )
    account = await _get_account(account_id)
    service = get_proxy_service_for_app(app_instance)
    fake_upstream = _FakeBridgeUpstreamWebSocket()
    select_calls: list[bool] = []

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
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        select_calls.append(prefer_earlier_reset_accounts)
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def fake_open_upstream_websocket_with_budget(self, target, headers, *, timeout_seconds):
        del self, target, headers, timeout_seconds
        return fake_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(
        proxy_module.ProxyService,
        "_open_upstream_websocket_with_budget",
        fake_open_upstream_websocket_with_budget,
    )

    payload = proxy_module.ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "",
            "input": "hello",
            "prompt_cache_key": "bridge_prefer_earlier_reset",
        }
    )
    affinity = proxy_module._sticky_key_for_responses_request(
        payload,
        {},
        codex_session_affinity=False,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
    )
    key = proxy_module._make_http_bridge_session_key(
        payload,
        headers={},
        affinity=affinity,
        api_key=None,
        request_id="req_bridge_prefer_earlier_reset",
    )

    session = await service._get_or_create_http_bridge_session(
        key,
        headers={},
        affinity=affinity,
        api_key=None,
        request_model=payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=8,
        gateway_safe_mode=True,
    )

    assert select_calls == [True]
    await service._close_http_bridge_session(session)


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_codex_session_prewarms_first_request(async_client, monkeypatch):
    _install_bridge_settings_with_limits(
        monkeypatch,
        enabled=True,
        codex_idle_ttl_seconds=600.0,
        codex_prewarm_enabled=True,
    )
    account_id = await _import_account(async_client, "acc_http_bridge_prewarm", "http-bridge-prewarm@example.com")
    account = await _get_account(account_id)
    fake_upstream = _PrewarmingBridgeUpstreamWebSocket()

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

    response = await async_client.post(
        "/v1/responses",
        headers={"x-codex-turn-state": "turn_state_prewarm"},
        json={
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] == "resp_actual_2"
    assert len(fake_upstream.sent_text) == 2
    assert json.loads(fake_upstream.sent_text[0])["generate"] is False
    assert "generate" not in json.loads(fake_upstream.sent_text[1])


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_codex_session_does_not_prewarm_by_default(async_client, monkeypatch):
    _install_bridge_settings_with_limits(monkeypatch, enabled=True, codex_idle_ttl_seconds=600.0)
    account_id = await _import_account(async_client, "acc_http_bridge_no_prewarm", "http-bridge-no-prewarm@example.com")
    account = await _get_account(account_id)
    fake_upstream = _PrewarmingBridgeUpstreamWebSocket()

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

    response = await async_client.post(
        "/v1/responses",
        headers={"x-codex-turn-state": "turn_state_no_prewarm"},
        json={
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        },
    )

    assert response.status_code == 200
    assert response.json()["id"] == "resp_actual_1"
    assert len(fake_upstream.sent_text) == 1
    assert "generate" not in json.loads(fake_upstream.sent_text[0])
