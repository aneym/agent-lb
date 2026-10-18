from __future__ import annotations
import asyncio
import json
import pytest
from sqlalchemy import select
import app.modules.proxy.service as proxy_module
from app.core.config.settings import Settings, get_settings
from app.db.models import Account, AccountStatus, DashboardSettings, RequestLog
from app.db.session import SessionLocal
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.test_http_responses_bridge import (
    _FakeBridgeUpstreamWebSocket,
    _assert_created_text_delta_completed,
    _cleanup_http_bridge_sessions,
    _get_account,
    _import_account,
    _install_bridge_settings,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_reuses_upstream_websocket_and_preserves_previous_response_id(
    async_client,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(async_client, "acc_http_bridge_reuse", "http-bridge-reuse@example.com")
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
        "prompt_cache_key": "http-bridge-thread-1",
    }
    monkeypatch.setenv("AGENT_LB_IDENTITY_OWNER", "owner-a")
    monkeypatch.setenv("AGENT_LB_IDENTITY_OWNER_MACHINES", "box-1")
    monkeypatch.setenv("AGENT_LB_IDENTITY_LOCAL_MACHINE", "box-1")
    get_settings.cache_clear()
    first = await async_client.post("/v1/responses", json=payload, headers={"x-agent-lb-machine": "box-1"})
    assert first.status_code == 200
    first_body = first.json()

    second = await async_client.post(
        "/v1/responses",
        json={**payload, "previous_response_id": first_body["id"]},
        headers={"x-agent-lb-machine": "box-2"},
    )
    get_settings.cache_clear()
    assert second.status_code == 200
    second_body = second.json()

    for _ in range(50):
        async with SessionLocal() as session:
            rows = (
                (
                    await session.execute(
                        select(RequestLog).where(RequestLog.request_id.in_(("resp_bridge_1", "resp_bridge_2")))
                    )
                )
                .scalars()
                .all()
            )
        if len(rows) == 2:
            break
        await asyncio.sleep(0.01)
    assert {
        row.request_id: (row.caller_user, row.caller_user_source, row.caller_machine, row.caller_machine_source)
        for row in rows
    } == {
        "resp_bridge_1": ("owner-a", "owner-machine", "box-1", "claimed"),
        "resp_bridge_2": ("unknown", "unknown", "box-2", "claimed"),
    }

    assert first_body["id"] == "resp_bridge_1"
    assert second_body["id"] == "resp_bridge_2"
    assert connect_calls == [(account_id, account.chatgpt_account_id)]
    assert len(fake_upstream.sent_text) == 2
    assert json.loads(fake_upstream.sent_text[1])["previous_response_id"] == "resp_bridge_1"


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_reuses_session_across_model_change_for_previous_response_id(
    async_client,
    monkeypatch,
):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_model_change",
        "http-bridge-model-change@example.com",
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

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    first = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
            "prompt_cache_key": "http-bridge-model-thread",
        },
    )
    assert first.status_code == 200
    first_body = first.json()

    second = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.4",
            "instructions": "Return exactly OK.",
            "input": "hello again",
            "prompt_cache_key": "http-bridge-model-thread",
            "previous_response_id": first_body["id"],
        },
    )
    assert second.status_code == 200

    assert connect_calls == [(account_id, account.chatgpt_account_id)]
    assert len(fake_upstream.sent_text) == 2
    second_payload = json.loads(fake_upstream.sent_text[1])
    assert second_payload["model"] == "gpt-5.4"
    assert second_payload["previous_response_id"] == first_body["id"]


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_recovers_previous_response_id_across_key_drift(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_live_session_required",
        "http-bridge-live-session-required@example.com",
    )
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

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    first = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
            "prompt_cache_key": "http-bridge-live-session-a",
        },
    )
    assert first.status_code == 200
    first_body = first.json()

    second = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello-again",
            "prompt_cache_key": "http-bridge-live-session-b",
            "previous_response_id": first_body["id"],
        },
    )

    assert second.status_code == 200
    assert second.json()["output"][0]["content"][0]["text"] == "OK"
    assert connect_count == 1


@pytest.mark.asyncio
async def test_v1_responses_http_emits_turn_state_header_and_reuses_when_replayed(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(
        async_client,
        "acc_v1_http_bridge_turn_state",
        "v1-http-bridge-turn-state@example.com",
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

    first = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello",
            "prompt_cache_key": "v1-http-turn-state-a",
        },
    )
    assert first.status_code == 200
    turn_state = first.headers["x-codex-turn-state"]
    first_body = first.json()

    second = await async_client.post(
        "/v1/responses",
        headers={"x-codex-turn-state": turn_state},
        json={
            "model": "gpt-5.1",
            "instructions": "Return exactly OK.",
            "input": "hello-again",
            "prompt_cache_key": "v1-http-turn-state-b",
            "previous_response_id": first_body["id"],
        },
    )
    assert second.status_code == 200

    assert turn_state.startswith("http_turn_")
    assert connect_calls == [("v1-http-turn-state-a", proxy_module.StickySessionKind.PROMPT_CACHE)]


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_streaming_path_uses_persistent_upstream_websocket(async_client, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(async_client, "acc_http_bridge_sse", "http-bridge-sse@example.com")
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
        "input": "hello",
        "prompt_cache_key": "http-bridge-sse-thread-1",
        "stream": True,
    }
    async with async_client.stream("POST", "/v1/responses", json=payload) as response:
        assert response.status_code == 200
        lines = [line async for line in response.aiter_lines() if line.startswith("data: ")]

    events = [json.loads(line[6:]) for line in lines if line[6:] != "[DONE]"]
    _assert_created_text_delta_completed(events)
    assert connect_count == 1
