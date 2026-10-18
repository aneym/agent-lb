from __future__ import annotations
import asyncio
import json
import time
from typing import Any, cast
import pytest
import app.modules.proxy.service as proxy_module
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.http_responses_bridge_streams import (
    _CreatedThenCloseUpstreamWebSocket,
    _ErrorOnlyUpstreamWebSocket,
    _RateLimitErrorUpstreamWebSocket,
)
from tests.integration.test_http_responses_bridge import (
    _cleanup_http_bridge_sessions,
    _get_account,
    _import_account,
    _install_bridge_settings,
    _install_proxy_settings,
    _make_app_settings,
    _make_dashboard_settings,
    _make_dummy_bridge_session,
    _wait_for_event,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_waits_for_inflight_session_before_continuity_error(app_instance, monkeypatch):
    service = get_proxy_service_for_app(app_instance)
    service._http_bridge_sessions.clear()
    service._http_bridge_inflight_sessions.clear()
    service._http_bridge_turn_state_index.clear()

    _install_proxy_settings(
        monkeypatch,
        app_settings=_make_app_settings(
            enabled=True,
            max_sessions=8,
            codex_idle_ttl_seconds=120.0,
            instance_id="instance-a",
            instance_ring=[],
        ),
        dashboard_settings=_make_dashboard_settings(),
    )

    create_started = asyncio.Event()
    release_create = asyncio.Event()

    async def fake_create_http_bridge_session(
        self,
        key,
        *,
        headers,
        affinity,
        api_key,
        request_model,
        idle_ttl_seconds,
        request_stage="first_turn",
        preferred_account_id=None,
        require_preferred_account=False,
        fallback_on_preferred_account_unavailable=True,
    ):
        del (
            self,
            headers,
            affinity,
            request_model,
            idle_ttl_seconds,
            request_stage,
            preferred_account_id,
            require_preferred_account,
            fallback_on_preferred_account_unavailable,
        )
        create_started.set()
        await _wait_for_event(release_create)
        return _make_dummy_bridge_session(key)

    monkeypatch.setattr(proxy_module.ProxyService, "_create_http_bridge_session", fake_create_http_bridge_session)

    key = proxy_module._HTTPBridgeSessionKey("request", "bridge-waits-for-inflight", None)

    creator = asyncio.create_task(
        service._get_or_create_http_bridge_session(
            key,
            headers={},
            affinity=proxy_module._AffinityPolicy(),
            api_key=None,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
            max_sessions=8,
        )
    )
    await _wait_for_event(create_started)

    follower = asyncio.create_task(
        service._get_or_create_http_bridge_session(
            key,
            headers={},
            affinity=proxy_module._AffinityPolicy(),
            api_key=None,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
            max_sessions=8,
            previous_response_id="resp_inflight",
        )
    )
    await asyncio.sleep(0.01)
    assert follower.done()

    release_create.set()
    created_session = await creator
    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await follower

    assert service._http_bridge_sessions[key] is created_session
    exc = exc_info.value
    assert exc.status_code == 502
    assert exc.payload["error"] == {
        "message": "Upstream websocket closed before response.completed",
        "type": "server_error",
        "code": "stream_incomplete",
    }


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_prunes_idle_session_before_reuse(app_instance, monkeypatch):
    service = get_proxy_service_for_app(app_instance)
    service._http_bridge_sessions.clear()
    service._http_bridge_inflight_sessions.clear()
    service._http_bridge_turn_state_index.clear()

    _install_proxy_settings(
        monkeypatch,
        app_settings=_make_app_settings(
            enabled=True,
            max_sessions=8,
            codex_idle_ttl_seconds=120.0,
            instance_id="instance-a",
            instance_ring=[],
        ),
        dashboard_settings=_make_dashboard_settings(),
    )

    create_started: list[str] = []

    async def fake_create_http_bridge_session(
        self,
        key,
        *,
        headers,
        affinity,
        api_key,
        request_model,
        idle_ttl_seconds,
        request_stage="first_turn",
        preferred_account_id=None,
        require_preferred_account=False,
        fallback_on_preferred_account_unavailable=True,
    ):
        del (
            self,
            headers,
            affinity,
            request_model,
            idle_ttl_seconds,
            request_stage,
            preferred_account_id,
            require_preferred_account,
            fallback_on_preferred_account_unavailable,
        )
        create_started.append(key.affinity_key)
        return _make_dummy_bridge_session(key)

    monkeypatch.setattr(proxy_module.ProxyService, "_create_http_bridge_session", fake_create_http_bridge_session)

    key = proxy_module._HTTPBridgeSessionKey("request", "bridge-idle-prune", None)
    stale_session = cast(proxy_module._HTTPBridgeSession, _make_dummy_bridge_session(key))
    stale_session.last_used_at = time.monotonic() - 300.0
    stale_session.idle_ttl_seconds = 120.0
    service._http_bridge_sessions[key] = stale_session

    try:
        replacement = await service._get_or_create_http_bridge_session(
            key,
            headers={},
            affinity=proxy_module._AffinityPolicy(),
            api_key=None,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
            max_sessions=8,
        )

        assert create_started == ["bridge-idle-prune"]
        assert replacement is not stale_session
        assert service._http_bridge_sessions[key] is replacement
    finally:
        service._http_bridge_sessions.clear()
        service._http_bridge_inflight_sessions.clear()
        service._http_bridge_turn_state_index.clear()


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_stream_failure_remains_valid_sse(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_sse_failure",
        "http-bridge-sse-failure@example.com",
    )
    account = await _get_account(account_id)
    upstream = _CreatedThenCloseUpstreamWebSocket()

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

    async with async_client.stream(
        "POST",
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "trigger-sse-failure",
            "prompt_cache_key": "sse-failure-key",
            "stream": True,
        },
    ) as response:
        assert response.status_code == 200
        lines = [line async for line in response.aiter_lines() if line.startswith("data: ")]

    events = [json.loads(line[6:]) for line in lines if line[6:] != "[DONE]"]
    assert [event["type"] for event in events] == ["response.created", "response.failed"]
    assert events[0]["response"]["id"] == events[-1]["response"]["id"]
    assert events[-1]["response"]["error"]["code"] == "stream_incomplete"


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_surfaces_upstream_error_event_as_http_400(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_error_norm",
        "http-bridge-error-norm@example.com",
    )
    account = await _get_account(account_id)
    fake_upstream = _ErrorOnlyUpstreamWebSocket()

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
        json={
            "model": "gpt-5.3-codex-spark",
            "instructions": "Return exactly OK.",
            "input": "hello",
            "prompt_cache_key": "http-bridge-error-norm-key",
            "stream": True,
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "message": "The 'gpt-5.3-codex-spark' model is not supported when using Codex with a ChatGPT account.",
            "type": "invalid_request_error",
            "code": "invalid_request_error",
        }
    }


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_preserves_rate_limit_metadata_in_429(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_ratelimit",
        "http-bridge-ratelimit@example.com",
    )
    account = await _get_account(account_id)
    fake_upstream = _RateLimitErrorUpstreamWebSocket()

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
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        return target

    async def fake_connect_responses_websocket(
        headers,
        access_token,
        account_id_header,
        *,
        base_url=None,
        session=None,
    ):
        return fake_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    response = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-4o",
            "instructions": "Return exactly OK.",
            "input": "hello",
            "prompt_cache_key": "http-bridge-ratelimit-key",
            "stream": True,
        },
    )

    assert response.status_code == 429
    body = response.json()
    assert body["error"]["code"] == "rate_limit_exceeded"
    assert body["error"]["plan_type"] == "team"
    assert body["error"]["resets_at"] == 1700000000
    assert body["error"]["resets_in_seconds"] == 3600
