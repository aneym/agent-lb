from __future__ import annotations
import asyncio
from typing import Any, cast
import pytest
from httpx import ASGITransport, AsyncClient
import app.modules.proxy.service as proxy_module
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.http_responses_bridge_anchor_errors import (
    _AnonymousPreviousResponseNotFoundWithInflightUpstreamWebSocket,
    _InvalidRequestPreviousResponseUpstreamWebSocket,
    _PreviousResponseNotFoundUpstreamWebSocket,
)
from tests.integration.test_http_responses_bridge import (
    _FakeBridgeUpstreamWebSocket,
    _TEST_SYNC_TIMEOUT_SECONDS,
    _cleanup_http_bridge_sessions,
    _get_account,
    _import_account,
    _install_bridge_settings,
    _replace_http_bridge_upstream_reader,
    _wait_for_event,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_rebinds_after_upstream_previous_response_not_found(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_previous_response_rebind",
        "http-bridge-previous-response-rebind@example.com",
    )
    account = await _get_account(account_id)
    first_upstream = _FakeBridgeUpstreamWebSocket()
    recovered_upstream = _FakeBridgeUpstreamWebSocket()
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
            api_key,
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
        if connect_count == 1:
            return first_upstream
        return recovered_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    first = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
            "prompt_cache_key": "previous-response-rebind",
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
            cast(proxy_module.UpstreamResponsesWebSocket, _PreviousResponseNotFoundUpstreamWebSocket()),
        )

    second = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello-again",
            "prompt_cache_key": "previous-response-rebind",
            "previous_response_id": first_body["id"],
        },
    )

    assert second.status_code == 200
    assert second.json()["output"][0]["content"][0]["text"] == "OK"
    assert connect_count == 2


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_rebinds_after_upstream_invalid_request_previous_response_not_found_param(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_invalid_request_rebind",
        "http-bridge-invalid-request-rebind@example.com",
    )
    account = await _get_account(account_id)
    first_upstream = _FakeBridgeUpstreamWebSocket()
    recovered_upstream = _FakeBridgeUpstreamWebSocket()
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
            api_key,
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
        if connect_count == 1:
            return first_upstream
        return recovered_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    first = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
            "prompt_cache_key": "invalid-request-rebind",
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
            cast(proxy_module.UpstreamResponsesWebSocket, _InvalidRequestPreviousResponseUpstreamWebSocket()),
        )

    second = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello-again",
            "prompt_cache_key": "invalid-request-rebind",
            "previous_response_id": first_body["id"],
        },
    )

    assert second.status_code == 200
    assert second.json()["output"][0]["content"][0]["text"] == "OK"
    assert connect_count == 2


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_masks_anonymous_previous_response_not_found_with_inflight_request(
    app_instance,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    upstream = _AnonymousPreviousResponseNotFoundWithInflightUpstreamWebSocket()
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
            api_key,
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
        return upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    async with app_instance.router.lifespan_context(app_instance):
        async with (
            AsyncClient(transport=ASGITransport(app=app_instance), base_url="http://testserver") as admin_client,
            AsyncClient(transport=ASGITransport(app=app_instance), base_url="http://testserver") as first_client,
            AsyncClient(transport=ASGITransport(app=app_instance), base_url="http://testserver") as second_client,
        ):
            account_id = await _import_account(
                admin_client,
                "acc_http_bridge_prev_nf_inflight",
                "http-bridge-prev-nf-inflight@example.com",
            )
            account = await _get_account(account_id)

            first = asyncio.create_task(
                first_client.post(
                    "/v1/responses",
                    json={
                        "model": "gpt-5.1",
                        "instructions": "Return exactly OK.",
                        "input": "hello",
                        "prompt_cache_key": "previous-response-inflight-mixed",
                    },
                )
            )
            await _wait_for_event(upstream.first_request_created)

            second = asyncio.create_task(
                second_client.post(
                    "/v1/responses",
                    json={
                        "model": "gpt-5.1",
                        "instructions": "Return exactly OK.",
                        "input": "hello-again",
                        "prompt_cache_key": "previous-response-inflight-mixed",
                        "previous_response_id": "resp_bridge_prev_anchor",
                    },
                )
            )

            first_response, second_response = await asyncio.wait_for(
                asyncio.gather(first, second),
                timeout=_TEST_SYNC_TIMEOUT_SECONDS,
            )

    assert first_response.status_code == 200
    assert first_response.json()["output"][0]["content"][0]["text"] == "OK"
    assert second_response.status_code >= 400
    assert second_response.json()["error"]["code"] == "stream_incomplete"
    assert "previous_response_not_found" not in second_response.json()["error"].get("code", "")
    assert "previous_response_not_found" not in second_response.json()["error"].get("message", "")
    assert connect_count == 1
