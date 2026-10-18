from __future__ import annotations
import pytest
import app.modules.proxy.service as proxy_module
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.http_responses_bridge_streams import (
    _SilentUpstreamWebSocket,
)
from tests.integration.test_http_responses_bridge import (
    _cleanup_http_bridge_sessions,
    _collect_sse_events_with_headers,
    _get_account,
    _import_account,
    _install_bridge_settings,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_kill_switch_falls_back_to_legacy_path(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=False)
    await _import_account(async_client, "acc_http_bridge_fallback", "http-bridge-fallback@example.com")
    seen = {"legacy": 0}

    async def fake_legacy_stream(
        payload,
        headers,
        access_token,
        account_id,
        base_url=None,
        raise_for_status=False,
        **_kw,
    ):
        del headers, access_token, account_id, base_url, raise_for_status, _kw
        seen["legacy"] += 1
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_legacy",'
            '"object":"response","status":"completed",'
            '"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2,"input_tokens_details":{"cached_tokens":0},'
            '"output_tokens_details":{"reasoning_tokens":0}}}}\n\n'
        )

    async def fail_connect(*args, **kwargs):
        raise AssertionError("bridge websocket path must not be used when the kill switch disables it")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_legacy_stream)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fail_connect)

    response = await async_client.post("/v1/responses", json={"model": "gpt-5.1", "input": "hi"})
    assert response.status_code == 200
    assert response.json()["id"] == "resp_legacy"
    assert "x-codex-turn-state" not in response.headers
    assert seen["legacy"] == 1


@pytest.mark.asyncio
async def test_backend_responses_http_bridge_kill_switch_falls_back_to_legacy_path(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=False)
    await _import_account(async_client, "acc_backend_http_bridge_fallback", "backend-http-bridge-fallback@example.com")
    seen = {"legacy": 0}

    async def fake_legacy_stream(
        payload,
        headers,
        access_token,
        account_id,
        base_url=None,
        raise_for_status=False,
        **_kw,
    ):
        del payload, headers, access_token, account_id, base_url, raise_for_status, _kw
        seen["legacy"] += 1
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_backend_legacy",'
            '"object":"response","status":"completed",'
            '"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2,'
            '"input_tokens_details":{"cached_tokens":0},"output_tokens_details":{"reasoning_tokens":0}}}}\n\n'
        )

    async def fail_connect(*args, **kwargs):
        raise AssertionError("bridge websocket path must not be used when the kill switch disables it")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_legacy_stream)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fail_connect)

    events, response_headers = await _collect_sse_events_with_headers(
        async_client,
        "/backend-api/codex/responses",
        json_body={"model": "gpt-5.1", "instructions": "hi", "input": "hello", "stream": True},
    )

    assert [event["type"] for event in events] == ["response.completed"]
    assert events[0]["response"]["id"] == "resp_backend_legacy"
    assert "x-codex-turn-state" not in response_headers
    assert seen["legacy"] == 1


@pytest.mark.asyncio
async def test_backend_responses_http_bridge_startup_error_omits_turn_state_header(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)

    response = await async_client.post(
        "/backend-api/codex/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
            "stream": True,
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "no_accounts"
    assert "x-codex-turn-state" not in response.headers


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_startup_error_omits_turn_state_header(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)

    response = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "no_accounts"
    assert "x-codex-turn-state" not in response.headers


@pytest.mark.asyncio
async def test_backend_responses_http_bridge_refresh_failure_returns_proxy_error(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_backend_http_bridge_refresh_failure",
        "backend-http-bridge-refresh-failure@example.com",
    )
    account = await _get_account(account_id)

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

    async def fail_refresh(self, target, *, force=False, timeout_seconds):
        del self, target, force, timeout_seconds
        raise proxy_module.RefreshError("refresh_token_expired", "token expired", True)

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fail_refresh)

    response = await async_client.post(
        "/backend-api/codex/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
            "stream": True,
        },
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"
    assert "x-codex-turn-state" not in response.headers


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_refresh_failure_returns_proxy_error(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_v1_http_bridge_refresh_failure",
        "v1-http-bridge-refresh-failure@example.com",
    )
    account = await _get_account(account_id)

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

    async def fail_refresh(self, target, *, force=False, timeout_seconds):
        del self, target, force, timeout_seconds
        raise proxy_module.RefreshError("refresh_token_expired", "token expired", True)

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fail_refresh)

    response = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
        },
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"
    assert "x-codex-turn-state" not in response.headers


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_transient_refresh_failure_returns_upstream_error(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_v1_http_bridge_refresh_transient_failure",
        "v1-http-bridge-refresh-transient-failure@example.com",
    )
    account = await _get_account(account_id)

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

    async def fail_refresh(self, target, *, force=False, timeout_seconds):
        del self, target, force, timeout_seconds
        raise proxy_module.RefreshError("invalid_response", "temporary refresh failure", False)

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fail_refresh)

    response = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
        },
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_unavailable"
    assert "x-codex-turn-state" not in response.headers


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_does_not_register_turn_state_alias_before_request_admission(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_alias_after_admission",
        "http-bridge-alias-after-admission@example.com",
    )
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

    async def fake_submit_http_bridge_request(
        self,
        session,
        *,
        request_state,
        text_data,
        queue_limit,
    ):
        del self, session, request_state, text_data, queue_limit
        raise proxy_module.ProxyResponseError(
            429,
            proxy_module.openai_error(
                "rate_limit_exceeded",
                "HTTP responses session bridge queue is full",
                error_type="rate_limit_error",
            ),
        )

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_submit_http_bridge_request", fake_submit_http_bridge_request)

    payload = proxy_module.ResponsesRequest(
        model="gpt-5.1",
        instructions="Return exactly OK.",
        input="hello",
        prompt_cache_key="bridge-alias-after-admission",
    )
    stream = service.stream_http_responses(
        payload,
        {},
        openai_cache_affinity=True,
        downstream_turn_state="http_turn_unadmitted",
    )

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await stream.__anext__()

    exc = exc_info.value
    assert exc.status_code == 429
    async with service._http_bridge_lock:
        sessions = list(service._http_bridge_sessions.values())
        assert len(sessions) == 1
        bridge_session = sessions[0]
        assert bridge_session.downstream_turn_state is None
        assert bridge_session.downstream_turn_state_aliases == set()
        assert service._http_bridge_turn_state_index == {}
