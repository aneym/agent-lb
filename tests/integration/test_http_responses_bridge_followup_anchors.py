from __future__ import annotations
import asyncio
import json
import pytest
from httpx import ASGITransport, AsyncClient
import app.modules.proxy.service as proxy_module
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.http_responses_bridge_followup_errors import (
    _TwoFollowupsPreviousResponseNotFoundUpstreamWebSocket,
    _TwoSameAnchorFollowupsPreviousResponseNotFoundUpstreamWebSocket,
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
async def test_v1_responses_http_bridge_stream_matches_previous_response_error_to_anchor_with_two_followups(
    app_instance,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    upstream = _TwoFollowupsPreviousResponseNotFoundUpstreamWebSocket()
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
            AsyncClient(transport=ASGITransport(app=app_instance), base_url="http://testserver") as fourth_client,
            AsyncClient(transport=ASGITransport(app=app_instance), base_url="http://testserver") as fifth_client,
        ):
            account_id = await _import_account(
                admin_client,
                "acc_http_bridge_two_followups_prev_nf",
                "http-bridge-two-followups-prev-nf@example.com",
            )
            account = await _get_account(account_id)

            first_response = await first_client.post(
                "/v1/responses",
                json={
                    "model": "gpt-5.1",
                    "instructions": "Return exactly OK.",
                    "input": "anchor-a",
                    "prompt_cache_key": "two-followups-prev-nf-stream",
                },
            )
            assert first_response.status_code == 200

            second_response = await second_client.post(
                "/v1/responses",
                json={
                    "model": "gpt-5.1",
                    "instructions": "Return exactly OK.",
                    "input": "anchor-b",
                    "prompt_cache_key": "two-followups-prev-nf-stream",
                },
            )
            assert second_response.status_code == 200

            third = asyncio.create_task(
                _collect_sse_events(
                    third_client,
                    "/v1/responses",
                    json_body={
                        "model": "gpt-5.1",
                        "instructions": "Return exactly OK.",
                        "input": "continue-a",
                        "prompt_cache_key": "two-followups-prev-nf-stream",
                        "previous_response_id": first_response.json()["id"],
                        "stream": True,
                    },
                )
            )
            await _wait_for_event(upstream.first_followup_created)

            fourth = asyncio.create_task(
                _collect_sse_events(
                    fourth_client,
                    "/v1/responses",
                    json_body={
                        "model": "gpt-5.1",
                        "instructions": "Return exactly OK.",
                        "input": "continue-b",
                        "prompt_cache_key": "two-followups-prev-nf-stream",
                        "previous_response_id": second_response.json()["id"],
                        "stream": True,
                    },
                )
            )

            third_events, fourth_events = await asyncio.wait_for(
                asyncio.gather(third, fourth),
                timeout=_TEST_SYNC_TIMEOUT_SECONDS,
            )

            fifth_events = await _collect_sse_events(
                fifth_client,
                "/v1/responses",
                json_body={
                    "model": "gpt-5.1",
                    "instructions": "Return exactly OK.",
                    "input": "after error",
                    "prompt_cache_key": "two-followups-prev-nf-stream",
                    "stream": True,
                },
            )

    assert [event["type"] for event in third_events] == ["response.created", "response.failed"]
    _assert_created_text_delta_completed(fourth_events)
    assert third_events[0]["response"]["id"] == "resp_bridge_followup_a"
    assert third_events[1]["response"]["id"] == "resp_bridge_followup_a"
    assert third_events[1]["response"]["error"]["code"] == "stream_incomplete"
    assert "previous_response_not_found" not in json.dumps(third_events[1])
    assert fourth_events[0]["response"]["id"] == "resp_bridge_followup_b"
    assert fourth_events[-1]["response"]["id"] == "resp_bridge_followup_b"
    assert fourth_events[-1]["response"]["output"][0]["content"][0]["text"] == "OK"
    _assert_created_text_delta_completed(fifth_events)
    assert fifth_events[-1]["response"]["output"][0]["content"][0]["text"] == "OK"
    assert connect_count == 1


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_stream_masks_anonymous_previous_response_not_found_for_same_anchor_followups(
    app_instance,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    upstream = _TwoSameAnchorFollowupsPreviousResponseNotFoundUpstreamWebSocket()
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
            AsyncClient(transport=ASGITransport(app=app_instance), base_url="http://testserver") as fourth_client,
        ):
            account_id = await _import_account(
                admin_client,
                "acc_http_bridge_two_same_anchor_followups_prev_nf",
                "http-bridge-two-same-anchor-followups-prev-nf@example.com",
            )
            account = await _get_account(account_id)

            first_response = await first_client.post(
                "/v1/responses",
                json={
                    "model": "gpt-5.1",
                    "instructions": "Return exactly OK.",
                    "input": "anchor",
                    "prompt_cache_key": "two-same-anchor-followups-prev-nf-stream",
                },
            )
            assert first_response.status_code == 200

            second = asyncio.create_task(
                _collect_sse_events(
                    second_client,
                    "/v1/responses",
                    json_body={
                        "model": "gpt-5.1",
                        "instructions": "Return exactly OK.",
                        "input": "continue-a",
                        "prompt_cache_key": "two-same-anchor-followups-prev-nf-stream",
                        "previous_response_id": first_response.json()["id"],
                        "stream": True,
                    },
                )
            )
            await _wait_for_event(upstream.first_followup_created)

            third = asyncio.create_task(
                _collect_sse_events(
                    third_client,
                    "/v1/responses",
                    json_body={
                        "model": "gpt-5.1",
                        "instructions": "Return exactly OK.",
                        "input": "continue-b",
                        "prompt_cache_key": "two-same-anchor-followups-prev-nf-stream",
                        "previous_response_id": first_response.json()["id"],
                        "stream": True,
                    },
                )
            )

            second_events, third_events = await asyncio.wait_for(
                asyncio.gather(second, third),
                timeout=_TEST_SYNC_TIMEOUT_SECONDS,
            )

            fourth_events = await _collect_sse_events(
                fourth_client,
                "/v1/responses",
                json_body={
                    "model": "gpt-5.1",
                    "instructions": "Return exactly OK.",
                    "input": "after-error",
                    "prompt_cache_key": "two-same-anchor-followups-prev-nf-stream",
                    "stream": True,
                },
            )

    assert [event["type"] for event in second_events] == ["response.created", "response.failed"]
    assert [event["type"] for event in third_events] == ["response.created", "response.failed"]
    assert second_events[0]["response"]["id"] == "resp_bridge_followup_same_anchor_a"
    assert third_events[0]["response"]["id"] == "resp_bridge_followup_same_anchor_b"
    assert second_events[1]["response"]["error"]["code"] == "stream_incomplete"
    assert third_events[1]["response"]["error"]["code"] == "stream_incomplete"
    assert "previous_response_not_found" not in json.dumps(second_events[1])
    assert "previous_response_not_found" not in json.dumps(third_events[1])
    _assert_created_text_delta_completed(fourth_events)
    assert fourth_events[-1]["response"]["id"] == "resp_bridge_after_same_anchor_error"
    assert connect_count == 1
