from __future__ import annotations
import json
import time
import pytest
import app.modules.proxy.service as proxy_module
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.http_responses_bridge_streams import (
    _TurnStateBridgeUpstreamWebSocket,
)
from tests.integration.test_http_responses_bridge import (
    _cleanup_http_bridge_sessions,
    _get_account,
    _import_account,
    _install_bridge_settings_with_limits,
    _make_api_key_data,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_reconnect_uses_last_upstream_turn_state(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_upstream_turn",
        "http-bridge-upstream-turn@example.com",
    )
    account = await _get_account(account_id)
    service = get_proxy_service_for_app(app_instance)
    connect_headers_seen: list[dict[str, str]] = []
    upstreams = [
        _TurnStateBridgeUpstreamWebSocket("upstream_turn_state_1"),
        _TurnStateBridgeUpstreamWebSocket("upstream_turn_state_2"),
        _TurnStateBridgeUpstreamWebSocket("upstream_turn_state_3"),
    ]

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
        del access_token, account_id_header, base_url, session
        connect_headers_seen.append(dict(headers))
        return upstreams.pop(0)

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    payload = proxy_module.ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )
    affinity = proxy_module._sticky_key_for_responses_request(
        payload,
        {"x-codex-turn-state": "local_turn_state"},
        codex_session_affinity=True,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    key = proxy_module._make_http_bridge_session_key(
        payload,
        headers={"x-codex-turn-state": "local_turn_state"},
        affinity=affinity,
        api_key=None,
        request_id="req_turn_state",
    )
    bridge_session = await service._get_or_create_http_bridge_session(
        key,
        headers={"x-codex-turn-state": "local_turn_state"},
        affinity=affinity,
        api_key=None,
        request_model=payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=8,
    )

    request_state = proxy_module._WebSocketRequestState(
        request_id="req-turn-state-reconnect",
        model=payload.model,
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        awaiting_response_created=True,
        response_create_gate_acquired=True,
        request_text=json.dumps({"type": "response.create", "model": "gpt-5.4", "input": []}),
    )
    await service._reconnect_http_bridge_session(bridge_session, request_state=request_state)

    assert connect_headers_seen[0]["x-codex-turn-state"] == "local_turn_state"
    assert connect_headers_seen[1]["x-codex-turn-state"] == "upstream_turn_state_1"
    assert bridge_session.upstream_turn_state == "upstream_turn_state_2"


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_session_id_reconnect_keeps_upstream_turn_state(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_session_reconnect",
        "http-bridge-session-reconnect@example.com",
    )
    account = await _get_account(account_id)
    service = get_proxy_service_for_app(app_instance)
    connect_headers_seen: list[dict[str, str]] = []
    upstreams = [
        _TurnStateBridgeUpstreamWebSocket("upstream_turn_state_1"),
        _TurnStateBridgeUpstreamWebSocket("upstream_turn_state_2"),
        _TurnStateBridgeUpstreamWebSocket("upstream_turn_state_3"),
    ]

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
        del access_token, account_id_header, base_url, session
        connect_headers_seen.append(dict(headers))
        return upstreams.pop(0)

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    payload = proxy_module.ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )
    headers = {"session_id": "session_http_bridge_1"}
    affinity = proxy_module._sticky_key_for_responses_request(
        payload,
        headers,
        codex_session_affinity=True,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    key = proxy_module._make_http_bridge_session_key(
        payload,
        headers=headers,
        affinity=affinity,
        api_key=None,
        request_id="req_session_turn_state",
    )
    bridge_session = await service._get_or_create_http_bridge_session(
        key,
        headers=headers,
        affinity=affinity,
        api_key=None,
        request_model=payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=8,
    )
    await service._register_http_bridge_turn_state(bridge_session, "http_turn_alias_session")

    request_state = proxy_module._WebSocketRequestState(
        request_id="req-session-turn-state-reconnect",
        model=payload.model,
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        awaiting_response_created=True,
        response_create_gate_acquired=True,
        request_text=json.dumps({"type": "response.create", "model": "gpt-5.4", "input": []}),
    )
    await service._reconnect_http_bridge_session(bridge_session, request_state=request_state)

    assert connect_headers_seen[0]["session_id"] == "session_http_bridge_1"
    assert "x-codex-turn-state" not in connect_headers_seen[0]
    assert connect_headers_seen[1]["x-codex-turn-state"] == "upstream_turn_state_1"
    assert bridge_session.downstream_turn_state == "http_turn_alias_session"
    assert bridge_session.upstream_turn_state == "upstream_turn_state_2"


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_reconnect_uses_refreshed_api_key_assignments_for_reused_session(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_assignment_refresh",
        "http-bridge-assignment-refresh@example.com",
    )
    account = await _get_account(account_id)
    service = get_proxy_service_for_app(app_instance)
    selection_assigned_account_ids: list[list[str]] = []
    upstreams = [
        _TurnStateBridgeUpstreamWebSocket("upstream_turn_state_1"),
        _TurnStateBridgeUpstreamWebSocket("upstream_turn_state_2"),
        _TurnStateBridgeUpstreamWebSocket("upstream_turn_state_3"),
    ]

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
        selection_assigned_account_ids.append(list(api_key.assigned_account_ids if api_key is not None else []))
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

    stale_api_key = _make_api_key_data(key_id="key_http_bridge_assignments", assigned_account_ids=["acc-stale"])
    refreshed_api_key = _make_api_key_data(
        key_id="key_http_bridge_assignments",
        assigned_account_ids=["acc-refreshed"],
    )
    payload = proxy_module.ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": [{"role": "user", "content": "hi"}]}
    )
    affinity = proxy_module._sticky_key_for_responses_request(
        payload,
        headers={"session_id": "session_http_bridge_assignment_refresh"},
        codex_session_affinity=True,
        openai_cache_affinity=True,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=stale_api_key,
    )
    key = proxy_module._make_http_bridge_session_key(
        payload,
        headers={"session_id": "session_http_bridge_assignment_refresh"},
        affinity=affinity,
        api_key=stale_api_key,
        request_id="req_assignment_refresh",
    )
    bridge_session = await service._get_or_create_http_bridge_session(
        key,
        headers={"session_id": "session_http_bridge_assignment_refresh"},
        affinity=affinity,
        api_key=stale_api_key,
        request_model=payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=8,
    )

    reused_session = await service._get_or_create_http_bridge_session(
        key,
        headers={"session_id": "session_http_bridge_assignment_refresh"},
        affinity=affinity,
        api_key=refreshed_api_key,
        request_model=payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=8,
    )
    assert reused_session is not bridge_session
    assert bridge_session.closed is True
    assert reused_session.api_key == refreshed_api_key

    request_state = proxy_module._WebSocketRequestState(
        request_id="req-assignment-refresh-reconnect",
        model=payload.model,
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        awaiting_response_created=True,
        api_key=refreshed_api_key,
        request_text=json.dumps({"type": "response.create", "model": "gpt-5.4", "input": []}),
    )
    await service._reconnect_http_bridge_session(reused_session, request_state=request_state)

    assert selection_assigned_account_ids == [["acc-stale"], ["acc-refreshed"], ["acc-refreshed"]]
