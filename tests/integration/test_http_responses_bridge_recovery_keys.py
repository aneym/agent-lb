from __future__ import annotations
import asyncio
import pytest
import app.modules.proxy.service as proxy_module
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.http_responses_bridge_streams import (
    _ClosingBridgeUpstreamWebSocket,
)
from tests.integration.test_http_responses_bridge import (
    _FakeBridgeUpstreamWebSocket,
    _TEST_SYNC_TIMEOUT_SECONDS,
    _cleanup_http_bridge_sessions,
    _get_account,
    _import_account,
    _install_bridge_settings,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_reconnects_after_clean_upstream_close(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(async_client, "acc_http_bridge_reconnect", "http-bridge-reconnect@example.com")
    account = await _get_account(account_id)
    first_upstream = _ClosingBridgeUpstreamWebSocket()
    second_upstream = _FakeBridgeUpstreamWebSocket()
    upstreams = [first_upstream, second_upstream]
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
        return upstream

    async def fail_legacy_stream(*args, **kwargs):
        raise AssertionError("legacy core_stream_responses path must not be used when HTTP bridge is enabled")

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)
    monkeypatch.setattr(proxy_module, "core_stream_responses", fail_legacy_stream)

    payload = {
        "model": "gpt-5.1",
        "instructions": "Return exactly OK.",
        "input": "hello",
        "prompt_cache_key": "http-bridge-reconnect-thread-1",
    }
    first = await asyncio.wait_for(async_client.post("/v1/responses", json=payload), timeout=_TEST_SYNC_TIMEOUT_SECONDS)
    second = await asyncio.wait_for(
        async_client.post("/v1/responses", json=payload), timeout=_TEST_SYNC_TIMEOUT_SECONDS
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert connect_count == 2


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_opens_fresh_session_for_previous_response_id_recovery(
    async_client, monkeypatch
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_previous_response_reconnect",
        "http-bridge-previous-response-reconnect@example.com",
    )
    account = await _get_account(account_id)
    first_upstream = _ClosingBridgeUpstreamWebSocket()
    second_upstream = _FakeBridgeUpstreamWebSocket()
    upstreams = [first_upstream, second_upstream]
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
        return upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    first = await asyncio.wait_for(
        async_client.post(
            "/v1/responses",
            json={
                "model": "gpt-5.1",
                "instructions": "Return exactly OK.",
                "input": "hello",
                "prompt_cache_key": "http-bridge-previous-response-reconnect",
            },
        ),
        timeout=_TEST_SYNC_TIMEOUT_SECONDS,
    )
    assert first.status_code == 200
    first_body = first.json()

    second = await asyncio.wait_for(
        async_client.post(
            "/v1/responses",
            json={
                "model": "gpt-5.1",
                "instructions": "Return exactly OK.",
                "input": "hello-again",
                "prompt_cache_key": "http-bridge-previous-response-reconnect",
                "previous_response_id": first_body["id"],
            },
        ),
        timeout=_TEST_SYNC_TIMEOUT_SECONDS,
    )

    assert second.status_code == 200
    assert second.json()["output"][0]["content"][0]["text"] == "OK"
    assert connect_count == 2


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_reuses_derived_prompt_cache_key_when_client_omits_it(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(async_client, "acc_http_bridge_derived", "http-bridge-derived@example.com")
    account = await _get_account(account_id)
    fake_upstream = _FakeBridgeUpstreamWebSocket()
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
        return fake_upstream

    async def fail_legacy_stream(*args, **kwargs):
        raise AssertionError("legacy core_stream_responses path must not be used when HTTP bridge is enabled")

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)
    monkeypatch.setattr(proxy_module, "core_stream_responses", fail_legacy_stream)

    payload = {
        "model": "gpt-5.1",
        "instructions": "Return exactly OK.",
        "input": "same-first-user-input",
    }
    first = await async_client.post("/v1/responses", json=payload)
    second = await async_client.post("/v1/responses", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert connect_count == 1


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_prefers_session_header_for_isolation(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_session_key",
        "http-bridge-session-key@example.com",
    )
    account = await _get_account(account_id)
    upstreams = [_FakeBridgeUpstreamWebSocket(), _FakeBridgeUpstreamWebSocket()]
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
        return upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    payload = {
        "model": "gpt-5.1",
        "instructions": "Return exactly OK.",
        "input": "same-first-user-input",
    }
    first = await async_client.post("/v1/responses", json=payload, headers={"session_id": "session-a"})
    second = await async_client.post("/v1/responses", json=payload, headers={"session_id": "session-b"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert connect_count == 2
