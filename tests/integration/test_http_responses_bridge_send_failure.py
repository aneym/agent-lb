from __future__ import annotations
from typing import Any, cast
import pytest
import app.modules.proxy.service as proxy_module
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.http_responses_bridge_streams import (
    _FailingSendThenCloseUpstreamWebSocket,
    _PrecreatedCloseUpstreamWebSocket,
)
from tests.integration.test_http_responses_bridge import (
    _FakeBridgeUpstreamWebSocket,
    _cleanup_http_bridge_sessions,
    _get_account,
    _import_account,
    _install_bridge_settings,
    _replace_http_bridge_upstream_reader,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_send_failure_returns_upstream_unavailable(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_send_failure_previous_response",
        "http-bridge-send-failure-previous-response@example.com",
    )
    account = await _get_account(account_id)
    fake_upstream = _FakeBridgeUpstreamWebSocket()
    failing_upstream = _FailingSendThenCloseUpstreamWebSocket()
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
        connect_count += 1
        return fake_upstream if connect_count == 1 else failing_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    first = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
            "prompt_cache_key": "send-failure-previous-response",
        },
    )
    assert first.status_code == 200
    first_body = first.json()

    service = get_proxy_service_for_app(app_instance)
    async with service._http_bridge_lock:
        session = next(iter(service._http_bridge_sessions.values()))
        session.upstream = cast(proxy_module.UpstreamResponsesWebSocket, failing_upstream)

    second = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello-again",
            "prompt_cache_key": "send-failure-previous-response",
            "previous_response_id": first_body["id"],
        },
    )

    assert second.status_code == 502
    assert second.json()["error"]["code"] in ("upstream_unavailable", "stream_incomplete", "bridge_owner_unreachable")
    assert "previous_response_not_found" not in second.json()["error"].get("code", "")
    assert connect_count == 1


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_precreated_disconnect_returns_upstream_unavailable(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_precreated_previous_response",
        "http-bridge-precreated-previous-response@example.com",
    )
    account = await _get_account(account_id)
    fake_upstream = _FakeBridgeUpstreamWebSocket()
    precreated_close_upstream = _PrecreatedCloseUpstreamWebSocket()
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
        connect_count += 1
        return fake_upstream if connect_count == 1 else precreated_close_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    first = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
            "prompt_cache_key": "precreated-previous-response",
        },
    )
    assert first.status_code == 200
    first_body = first.json()

    service = get_proxy_service_for_app(app_instance)
    async with service._http_bridge_lock:
        session = next(iter(service._http_bridge_sessions.values()))
        await _replace_http_bridge_upstream_reader(
            service,
            session,
            cast(proxy_module.UpstreamResponsesWebSocket, precreated_close_upstream),
        )

    second = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello-again",
            "prompt_cache_key": "precreated-previous-response",
            "previous_response_id": first_body["id"],
        },
    )

    assert second.status_code == 502
    assert second.json()["error"]["code"] in ("upstream_unavailable", "stream_incomplete", "upstream_request_timeout")
    assert "previous_response_not_found" not in second.json()["error"].get("code", "")
    assert connect_count == 1
