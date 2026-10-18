from __future__ import annotations
import asyncio
import json
import pytest
from httpx import ASGITransport, AsyncClient
import app.modules.proxy.service as proxy_module
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.http_responses_bridge_anchor_errors import (
    _AnonymousPreviousResponseNotFoundAfterCreatedUpstreamWebSocket,
    _ForeignPreviousResponseNotFoundAfterCreatedUpstreamWebSocket,
)
from tests.integration.test_http_responses_bridge import (
    _TEST_SYNC_TIMEOUT_SECONDS,
    _assert_created_text_delta_completed,
    _cleanup_http_bridge_sessions,
    _collect_sse_events,
    _get_account,
    _import_account,
    _install_bridge_settings,
    _wait_for_event,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_keeps_session_alive_after_foreign_previous_response_not_found(
    async_client,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_foreign_prev_nf_created",
        "http-bridge-foreign-prev-nf-created@example.com",
    )
    account = await _get_account(account_id)
    upstream = _ForeignPreviousResponseNotFoundAfterCreatedUpstreamWebSocket()
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

    first = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
            "prompt_cache_key": "foreign-previous-response-created",
        },
    )
    assert first.status_code == 200
    first_body = first.json()

    second = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "continue",
            "prompt_cache_key": "foreign-previous-response-created",
            "previous_response_id": first_body["id"],
        },
    )

    third = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "after error",
            "prompt_cache_key": "foreign-previous-response-created",
        },
    )

    assert second.status_code == 502
    assert second.json()["error"]["code"] == "stream_incomplete"
    assert "previous_response_not_found" not in second.json()["error"].get("code", "")
    assert "previous_response_not_found" not in second.json()["error"].get("message", "")
    assert third.status_code == 200
    assert third.json()["output"][0]["content"][0]["text"] == "OK"
    assert connect_count == 1
    assert upstream.closed is False


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_stream_keeps_session_alive_after_foreign_previous_response_not_found(
    async_client,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_foreign_prev_nf_created_stream",
        "http-bridge-foreign-prev-nf-created-stream@example.com",
    )
    account = await _get_account(account_id)
    upstream = _ForeignPreviousResponseNotFoundAfterCreatedUpstreamWebSocket()
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

    first_events = await _collect_sse_events(
        async_client,
        "/v1/responses",
        json_body={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
            "prompt_cache_key": "foreign-previous-response-created-stream",
            "stream": True,
        },
    )
    first_response = first_events[-1]["response"]

    second_events = await _collect_sse_events(
        async_client,
        "/v1/responses",
        json_body={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "continue",
            "prompt_cache_key": "foreign-previous-response-created-stream",
            "previous_response_id": first_response["id"],
            "stream": True,
        },
    )

    third_events = await _collect_sse_events(
        async_client,
        "/v1/responses",
        json_body={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "after error",
            "prompt_cache_key": "foreign-previous-response-created-stream",
            "stream": True,
        },
    )

    _assert_created_text_delta_completed(first_events)
    assert [event["type"] for event in second_events] == ["response.created", "response.failed"]
    _assert_created_text_delta_completed(third_events)
    assert second_events[-1]["response"]["error"]["code"] == "stream_incomplete"
    assert "previous_response_not_found" not in json.dumps(second_events[-1])
    assert third_events[-1]["response"]["output"][0]["content"][0]["text"] == "OK"
    assert connect_count == 1
    assert upstream.closed is False


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_stream_keeps_session_alive_after_anonymous_prev_nf_created_followup(
    app_instance,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    upstream = _AnonymousPreviousResponseNotFoundAfterCreatedUpstreamWebSocket()
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
            AsyncClient(transport=ASGITransport(app=app_instance), base_url="http://testserver") as third_client,
        ):
            account_id = await _import_account(
                admin_client,
                "acc_http_bridge_anonymous_prev_nf_created_followup",
                "http-bridge-anonymous-prev-nf-created-followup@example.com",
            )
            account = await _get_account(account_id)

            first = asyncio.create_task(
                _collect_sse_events(
                    first_client,
                    "/v1/responses",
                    json_body={
                        "model": "gpt-5.1",
                        "instructions": "Return exactly OK.",
                        "input": "hello",
                        "prompt_cache_key": "anonymous-created-followup-stream",
                        "stream": True,
                    },
                )
            )
            await _wait_for_event(upstream.first_request_created)

            second = asyncio.create_task(
                _collect_sse_events(
                    second_client,
                    "/v1/responses",
                    json_body={
                        "model": "gpt-5.1",
                        "instructions": "Return exactly OK.",
                        "input": "continue",
                        "prompt_cache_key": "anonymous-created-followup-stream",
                        "previous_response_id": "resp_bridge_prev_anchor",
                        "stream": True,
                    },
                )
            )

            first_events, second_events = await asyncio.wait_for(
                asyncio.gather(first, second),
                timeout=_TEST_SYNC_TIMEOUT_SECONDS,
            )

            third_events = await _collect_sse_events(
                third_client,
                "/v1/responses",
                json_body={
                    "model": "gpt-5.1",
                    "instructions": "Return exactly OK.",
                    "input": "after error",
                    "prompt_cache_key": "anonymous-created-followup-stream",
                    "stream": True,
                },
            )

    _assert_created_text_delta_completed(first_events)
    assert [event["type"] for event in second_events] == ["response.created", "response.failed"]
    assert second_events[0]["response"]["id"] == "resp_bridge_followup_created"
    assert second_events[1]["response"]["id"] == "resp_bridge_followup_created"
    assert second_events[1]["response"]["error"]["code"] == "stream_incomplete"
    assert "previous_response_not_found" not in json.dumps(second_events[1])
    _assert_created_text_delta_completed(third_events)
    assert third_events[-1]["response"]["output"][0]["content"][0]["text"] == "OK"
    assert connect_count == 1
