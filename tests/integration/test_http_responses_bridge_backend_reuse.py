from __future__ import annotations
import json
import pytest
import app.modules.proxy.service as proxy_module
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.test_http_responses_bridge import (
    _FakeBridgeUpstreamWebSocket,
    _assert_created_text_delta_completed,
    _cleanup_http_bridge_sessions,
    _collect_sse_events,
    _collect_sse_events_with_headers,
    _get_account,
    _import_account,
    _install_bridge_settings,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_backend_responses_http_bridge_reuses_upstream_websocket_and_preserves_previous_response_id(
    async_client,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_backend_http_bridge_reuse",
        "backend-http-bridge-reuse@example.com",
    )
    account = await _get_account(account_id)
    fake_upstream = _FakeBridgeUpstreamWebSocket()
    connect_calls: list[tuple[str | None, str | None]] = []

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
        del headers, access_token, base_url, session
        connect_calls.append((account_id, account_id_header))
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
        "input": "hello",
        "prompt_cache_key": "backend-http-bridge-thread-1",
        "stream": True,
    }
    first_events = await _collect_sse_events(async_client, "/backend-api/codex/responses", json_body=payload)
    first_response = first_events[-1]["response"]

    second_events = await _collect_sse_events(
        async_client,
        "/backend-api/codex/responses",
        json_body={**payload, "previous_response_id": first_response["id"]},
    )
    second_response = second_events[-1]["response"]

    _assert_created_text_delta_completed(first_events)
    _assert_created_text_delta_completed(second_events)
    assert first_response["id"] == "resp_bridge_1"
    assert second_response["id"] == "resp_bridge_2"
    assert connect_calls == [(account_id, account.chatgpt_account_id)]
    assert len(fake_upstream.sent_text) == 2
    assert json.loads(fake_upstream.sent_text[1])["previous_response_id"] == "resp_bridge_1"


@pytest.mark.asyncio
async def test_backend_responses_http_bridge_prefers_codex_session_header_over_prompt_cache_key(
    async_client,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_backend_http_bridge_session_header",
        "backend-http-bridge-session-header@example.com",
    )
    account = await _get_account(account_id)
    fake_upstream = _FakeBridgeUpstreamWebSocket()
    connect_calls: list[tuple[str | None, proxy_module.StickySessionKind | None]] = []

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
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        connect_calls.append((sticky_key, sticky_kind))
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

    headers = {"session_id": "backend-http-session-1"}
    first_events = await _collect_sse_events(
        async_client,
        "/backend-api/codex/responses",
        json_body={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
            "prompt_cache_key": "backend-http-prompt-a",
            "stream": True,
        },
        headers=headers,
    )
    first_response = first_events[-1]["response"]

    second_events = await _collect_sse_events(
        async_client,
        "/backend-api/codex/responses",
        json_body={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello-again",
            "prompt_cache_key": "backend-http-prompt-b",
            "previous_response_id": first_response["id"],
            "stream": True,
        },
        headers=headers,
    )

    _assert_created_text_delta_completed(first_events)
    _assert_created_text_delta_completed(second_events)
    assert len(connect_calls) == 1
    assert connect_calls[0] == ("backend-http-session-1", proxy_module.StickySessionKind.CODEX_SESSION)
    assert len(fake_upstream.sent_text) == 2
    assert json.loads(fake_upstream.sent_text[1])["prompt_cache_key"] == "backend-http-prompt-b"


@pytest.mark.asyncio
async def test_backend_responses_http_emits_turn_state_header_and_reuses_when_replayed(
    async_client,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_backend_http_bridge_turn_state",
        "backend-http-bridge-turn-state@example.com",
    )
    account = await _get_account(account_id)
    fake_upstream = _FakeBridgeUpstreamWebSocket()
    connect_calls: list[tuple[str | None, proxy_module.StickySessionKind | None]] = []

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
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset_accounts,
            routing_strategy,
            model,
            exclude_account_ids,
            additional_limit_name,
        )
        connect_calls.append((sticky_key, sticky_kind))
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

    first_events, first_headers = await _collect_sse_events_with_headers(
        async_client,
        "/backend-api/codex/responses",
        json_body={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
            "prompt_cache_key": "backend-http-turn-state-a",
            "stream": True,
        },
    )
    turn_state = first_headers["x-codex-turn-state"]
    first_response = first_events[-1]["response"]

    second_events = await _collect_sse_events(
        async_client,
        "/backend-api/codex/responses",
        json_body={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello-again",
            "prompt_cache_key": "backend-http-turn-state-b",
            "previous_response_id": first_response["id"],
            "stream": True,
        },
        headers={"x-codex-turn-state": turn_state},
    )

    _assert_created_text_delta_completed(first_events)
    _assert_created_text_delta_completed(second_events)
    assert turn_state.startswith("http_turn_")
    assert connect_calls == [("backend-http-turn-state-a", proxy_module.StickySessionKind.PROMPT_CACHE)]
