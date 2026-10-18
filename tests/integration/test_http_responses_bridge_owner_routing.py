from __future__ import annotations
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
import pytest
import app.modules.proxy.service as proxy_module
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.test_http_responses_bridge import (
    _FakeBridgeUpstreamWebSocket,
    _cleanup_http_bridge_sessions,
    _get_account,
    _import_account,
    _install_bridge_settings_with_limits,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_non_owner_instance_falls_back_to_local_session(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(
        monkeypatch,
        enabled=True,
        gateway_safe_mode=True,
        instance_id="instance-b",
        instance_ring=["instance-a", "instance-b"],
    )
    account_id = await _import_account(async_client, "acc_http_bridge_owner", "http-bridge-owner@example.com")
    account = await _get_account(account_id)
    service = get_proxy_service_for_app(app_instance)
    service._ring_membership = cast(
        proxy_module.RingMembershipService,
        SimpleNamespace(list_active=AsyncMock(return_value=["instance-a", "instance-b"])),
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

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        del headers, access_token, account_id_header, base_url, session
        return _FakeBridgeUpstreamWebSocket()

    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    candidate_suffix = 0
    while True:
        payload = proxy_module.ResponsesRequest.model_validate(
            {
                "model": "gpt-5.4",
                "instructions": "hi",
                "input": [{"role": "user", "content": "hi"}],
                "prompt_cache_key": f"owner-check-{candidate_suffix}",
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
            request_id="req_owner",
        )
        owner = await proxy_module._http_bridge_owner_instance(key, proxy_module.get_settings())
        if owner != "instance-b":
            break
        candidate_suffix += 1

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

    assert session is not None


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_non_owner_prompt_cache_rebinds_locally_when_gateway_safe_mode_disabled(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(
        monkeypatch,
        enabled=True,
        gateway_safe_mode=False,
        instance_id="instance-b",
        instance_ring=["instance-a", "instance-b"],
    )
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_owner_strict",
        "http-bridge-owner-strict@example.com",
    )
    account = await _get_account(account_id)
    service = get_proxy_service_for_app(app_instance)
    service._ring_membership = cast(
        proxy_module.RingMembershipService,
        SimpleNamespace(list_active=AsyncMock(return_value=["instance-a", "instance-b"])),
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
        api_key=None,
        preferred_account_id=None,
        exclude_account_ids=None,
        additional_limit_name=None,
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
            api_key,
            exclude_account_ids,
            additional_limit_name,
        )
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(
        proxy_module,
        "connect_responses_websocket",
        AsyncMock(return_value=_FakeBridgeUpstreamWebSocket()),
    )

    candidate_suffix = 0
    while True:
        payload = proxy_module.ResponsesRequest.model_validate(
            {
                "model": "gpt-5.4",
                "instructions": "hi",
                "input": [{"role": "user", "content": "hi"}],
                "prompt_cache_key": f"owner-check-strict-{candidate_suffix}",
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
            request_id="req_owner_strict",
        )
        owner = await proxy_module._http_bridge_owner_instance(key, proxy_module.get_settings())
        if owner != "instance-b":
            break
        candidate_suffix += 1

    session = await service._get_or_create_http_bridge_session(
        key,
        headers={},
        affinity=affinity,
        api_key=None,
        request_model=payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=8,
        gateway_safe_mode=False,
    )

    assert session.account.id == account.id
    assert session.key == key


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_allows_unstable_request_key_even_on_non_owner_instance(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(
        monkeypatch,
        enabled=True,
        instance_id="instance-b",
        instance_ring=["instance-a", "instance-b"],
    )
    account_id = await _import_account(async_client, "acc_http_bridge_unstable", "http-bridge-unstable@example.com")
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
        {},
        codex_session_affinity=False,
        openai_cache_affinity=False,
        openai_cache_affinity_max_age_seconds=300,
        sticky_threads_enabled=False,
        api_key=None,
    )
    key = proxy_module._make_http_bridge_session_key(
        payload,
        headers={},
        affinity=affinity,
        api_key=None,
        request_id="req_owner_unstable",
    )

    session = await service._get_or_create_http_bridge_session(
        key,
        headers={},
        affinity=affinity,
        api_key=None,
        request_model=payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=8,
    )

    assert session.key.affinity_kind == "request"
