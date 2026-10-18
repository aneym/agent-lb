from __future__ import annotations
import asyncio
from types import SimpleNamespace
from typing import Any, cast
import pytest
import app.modules.proxy.service as proxy_module
from app.db.models import Account, AccountStatus, DashboardSettings, RequestLog
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.test_http_responses_bridge import (
    _FakeBridgeUpstreamWebSocket,
    _cleanup_http_bridge_sessions,
    _get_account,
    _import_account,
    _install_bridge_settings_with_limits,
    _make_api_key_data,
    _make_dummy_bridge_session,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_turn_state_alias_respects_api_key_isolation(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_api_key_alias",
        "http-bridge-api-key-alias@example.com",
    )
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

    payload = proxy_module.ResponsesRequest(
        model="gpt-5.1",
        instructions="Return exactly OK.",
        input="hello",
        prompt_cache_key="api-key-alias-thread",
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
    api_key_a = cast(proxy_module.ApiKeyData, SimpleNamespace(id="api-key-a"))
    session = await service._get_or_create_http_bridge_session(
        proxy_module._make_http_bridge_session_key(
            payload,
            headers={},
            affinity=affinity,
            api_key=api_key_a,
            request_id="req_api_key_alias",
        ),
        headers={},
        affinity=affinity,
        api_key=api_key_a,
        request_model=payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=128,
    )
    await service._register_http_bridge_turn_state(session, "http_turn_api_key_alias")

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service._get_or_create_http_bridge_session(
            proxy_module._HTTPBridgeSessionKey("turn_state_header", "http_turn_api_key_alias", "api-key-b"),
            headers={"x-codex-turn-state": "http_turn_api_key_alias"},
            affinity=proxy_module._AffinityPolicy(
                key="http_turn_api_key_alias",
                kind=proxy_module.StickySessionKind.CODEX_SESSION,
            ),
            api_key=cast(proxy_module.ApiKeyData, SimpleNamespace(id="api-key-b")),
            request_model=payload.model,
            idle_ttl_seconds=120.0,
            max_sessions=128,
        )

    assert isinstance(exc_info.value, proxy_module.ProxyResponseError)
    exc = exc_info.value
    assert exc.status_code == 409
    assert exc.payload["error"].get("code") == "bridge_instance_mismatch"
    await service._close_http_bridge_session(session)


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_closes_disallowed_session_before_owner_mismatch_retry(
    app_instance, monkeypatch
):
    _install_bridge_settings_with_limits(
        monkeypatch,
        enabled=True,
        instance_id="instance-a",
        instance_ring=["instance-a", "instance-b"],
    )
    service = get_proxy_service_for_app(app_instance)
    key = proxy_module._HTTPBridgeSessionKey("session_header", "shared-session", "key-assignments")
    stale_api_key = _make_api_key_data(key_id="key-assignments", assigned_account_ids=["acc-stale"])
    refreshed_api_key = _make_api_key_data(key_id="key-assignments", assigned_account_ids=["acc-fresh"])
    upstream = _FakeBridgeUpstreamWebSocket()
    stale_session = cast(proxy_module._HTTPBridgeSession, _make_dummy_bridge_session(key))
    alias_key = proxy_module._http_bridge_turn_state_alias_key("http_turn_owner_retry", key.api_key_id)

    cast(Any, stale_session).account = SimpleNamespace(id="acc-stale", status=AccountStatus.ACTIVE)
    cast(Any, stale_session).api_key = stale_api_key
    cast(Any, stale_session).upstream = upstream
    stale_session.downstream_turn_state_aliases.add("http_turn_owner_retry")
    service._http_bridge_sessions[key] = stale_session
    service._http_bridge_turn_state_index[alias_key] = key

    async def fake_http_bridge_owner_instance(session_key, settings, ring_membership=None):
        del settings, ring_membership
        assert session_key == key
        return "instance-b"

    async def fake_active_http_bridge_instance_ring(settings, ring_membership):
        del settings, ring_membership
        return "instance-a", ("instance-a", "instance-b")

    monkeypatch.setattr(proxy_module, "_http_bridge_owner_instance", fake_http_bridge_owner_instance)
    monkeypatch.setattr(proxy_module, "_active_http_bridge_instance_ring", fake_active_http_bridge_instance_ring)

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service._get_or_create_http_bridge_session(
            key,
            headers={"session_id": "shared-session"},
            affinity=proxy_module._AffinityPolicy(
                key="shared-session",
                kind=proxy_module.StickySessionKind.CODEX_SESSION,
            ),
            api_key=refreshed_api_key,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
            max_sessions=8,
        )

    exc = exc_info.value
    if exc.status_code == 409:
        assert exc.payload["error"].get("code") == "bridge_instance_mismatch"
    else:
        assert exc.status_code == 503
    assert key not in service._http_bridge_inflight_sessions
    assert key not in service._http_bridge_sessions
    assert alias_key not in service._http_bridge_turn_state_index
    assert stale_session.closed is True
    assert upstream.closed is True


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_preserves_prior_turn_state_aliases(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_alias_preserve",
        "http-bridge-alias-preserve@example.com",
    )
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

    payload = proxy_module.ResponsesRequest(
        model="gpt-5.1",
        instructions="Return exactly OK.",
        input="hello",
        prompt_cache_key="alias-preserve-thread",
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
    session = await service._get_or_create_http_bridge_session(
        proxy_module._make_http_bridge_session_key(
            payload,
            headers={},
            affinity=affinity,
            api_key=None,
            request_id="req_alias_preserve",
        ),
        headers={},
        affinity=affinity,
        api_key=None,
        request_model=payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=128,
    )

    await service._register_http_bridge_turn_state(session, "http_turn_alias_a")
    await service._register_http_bridge_turn_state(session, "http_turn_alias_b")

    replayed = await service._get_or_create_http_bridge_session(
        proxy_module._HTTPBridgeSessionKey("turn_state_header", "http_turn_alias_a", None),
        headers={"x-codex-turn-state": "http_turn_alias_a"},
        affinity=proxy_module._AffinityPolicy(
            key="http_turn_alias_a",
            kind=proxy_module.StickySessionKind.CODEX_SESSION,
        ),
        api_key=None,
        request_model=payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=128,
    )

    assert replayed is session
    assert "http_turn_alias_a" in replayed.downstream_turn_state_aliases
    assert "http_turn_alias_b" in replayed.downstream_turn_state_aliases
    await service._close_http_bridge_session(session)


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_close_waits_for_turn_state_index_lock(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_close_lock",
        "http-bridge-close-lock@example.com",
    )
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

    payload = proxy_module.ResponsesRequest.model_validate({"model": "gpt-5.1", "instructions": "hi", "input": []})
    affinity = proxy_module._AffinityPolicy(key="turn-close-lock", kind=proxy_module.StickySessionKind.CODEX_SESSION)

    session = await service._get_or_create_http_bridge_session(
        proxy_module._make_http_bridge_session_key(
            payload,
            headers={},
            affinity=affinity,
            api_key=None,
            request_id="req_close_lock",
        ),
        headers={},
        affinity=affinity,
        api_key=None,
        request_model=payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=128,
    )
    await service._register_http_bridge_turn_state(session, "http_turn_close_lock")

    alias_key = proxy_module._http_bridge_turn_state_alias_key("http_turn_close_lock", session.key.api_key_id)

    async with service._http_bridge_lock:
        close_task = asyncio.create_task(service._close_http_bridge_session(session))
        await asyncio.sleep(0)
        assert not close_task.done()
        assert service._http_bridge_turn_state_index[alias_key] == session.key

    await close_task

    assert alias_key not in service._http_bridge_turn_state_index
