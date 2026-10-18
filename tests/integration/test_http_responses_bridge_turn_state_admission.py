from __future__ import annotations
import asyncio
import time
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
    _make_dummy_bridge_session,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_missing_turn_state_alias_with_previous_response_id_fails_closed(
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(monkeypatch, enabled=True)
    service = get_proxy_service_for_app(app_instance)
    service._http_bridge_sessions.clear()
    service._http_bridge_inflight_sessions.clear()
    service._http_bridge_turn_state_index.clear()

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service._get_or_create_http_bridge_session(
            proxy_module._HTTPBridgeSessionKey("turn_state_header", "http_turn_missing_alias", None),
            headers={"x-codex-turn-state": "http_turn_missing_alias"},
            affinity=proxy_module._AffinityPolicy(
                key="http_turn_missing_alias",
                kind=proxy_module.StickySessionKind.CODEX_SESSION,
            ),
            api_key=None,
            request_model="gpt-5.1",
            idle_ttl_seconds=120.0,
            max_sessions=128,
            previous_response_id="resp_missing_alias",
        )

    exc = exc_info.value
    assert exc.status_code == 502
    assert exc.payload["error"] == {
        "message": "Upstream websocket closed before response.completed",
        "type": "server_error",
        "code": "stream_incomplete",
    }


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_replayed_turn_state_alias_preserves_owner_without_rekeying_session(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(
        monkeypatch,
        enabled=True,
        codex_idle_ttl_seconds=600.0,
        instance_id="instance-a",
        instance_ring=["instance-a", "instance-b"],
    )
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_alias_owner",
        "http-bridge-alias-owner@example.com",
    )
    account = await _get_account(account_id)
    service = get_proxy_service_for_app(app_instance)
    upstreams = [_FakeBridgeUpstreamWebSocket(), _FakeBridgeUpstreamWebSocket()]
    connect_headers_seen: list[dict[str, str]] = []

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
        del access_token, account_id_header, base_url, session
        connect_headers_seen.append(dict(headers))
        return upstreams.pop(0)

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    candidate_suffix = 0
    while True:
        payload = proxy_module.ResponsesRequest(
            model="gpt-5.1",
            instructions="Return exactly OK.",
            input="hello",
            prompt_cache_key=f"owner-alias-thread-{candidate_suffix}",
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
            request_id="req_owner_alias",
        )
        if await proxy_module._http_bridge_owner_instance(key, proxy_module.get_settings()) == "instance-a":
            break
        candidate_suffix += 1

    session = await service._get_or_create_http_bridge_session(
        key,
        headers={},
        affinity=affinity,
        api_key=None,
        request_model=payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=128,
    )

    replay_turn_state = None
    for candidate in ("turn_owner_alias_b", "turn_owner_alias_c", "turn_owner_alias_d", "turn_owner_alias_e"):
        if (
            await proxy_module._http_bridge_owner_instance(
                proxy_module._HTTPBridgeSessionKey("turn_state_header", candidate, None),
                proxy_module.get_settings(),
            )
            == "instance-b"
        ):
            replay_turn_state = candidate
            break
    assert replay_turn_state is not None
    await service._register_http_bridge_turn_state(session, replay_turn_state)
    replay_key = proxy_module._HTTPBridgeSessionKey("turn_state_header", replay_turn_state, None)
    assert (
        service._http_bridge_turn_state_index[
            proxy_module._http_bridge_turn_state_alias_key(replay_turn_state, session.key.api_key_id)
        ]
        == key
    )

    replayed = await service._get_or_create_http_bridge_session(
        replay_key,
        headers={"x-codex-turn-state": replay_turn_state},
        affinity=proxy_module._AffinityPolicy(key=replay_turn_state, kind=proxy_module.StickySessionKind.CODEX_SESSION),
        api_key=None,
        request_model=payload.model,
        idle_ttl_seconds=120.0,
        max_sessions=128,
    )

    assert replayed is session
    assert replayed.key == key
    assert key in service._http_bridge_sessions
    assert replay_key not in service._http_bridge_sessions
    assert (
        service._http_bridge_turn_state_index[
            proxy_module._http_bridge_turn_state_alias_key(replay_turn_state, session.key.api_key_id)
        ]
        == key
    )
    assert replayed.codex_session is True
    assert replayed.affinity.kind == proxy_module.StickySessionKind.CODEX_SESSION
    assert replayed.affinity.key == replay_turn_state
    assert replayed.idle_ttl_seconds >= 600.0
    replayed.upstream_turn_state = "upstream_turn_state_stale"
    request_state = proxy_module._WebSocketRequestState(
        request_id="req_owner_alias_reconnect",
        model=payload.model,
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
    )
    await service._reconnect_http_bridge_session(replayed, request_state=request_state)
    assert connect_headers_seen[-1]["x-codex-turn-state"] == replay_turn_state
    await service._close_http_bridge_session(session)


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_waits_for_inflight_recreation_on_missing_turn_state_alias(app_instance):
    service = get_proxy_service_for_app(app_instance)
    service._http_bridge_sessions.clear()
    service._http_bridge_turn_state_index.clear()
    service._http_bridge_inflight_sessions.clear()

    replay_turn_state = "http_turn_inflight_replay"
    replay_key = proxy_module._HTTPBridgeSessionKey("turn_state_header", replay_turn_state, None)
    expected_session = _make_dummy_bridge_session(replay_key)
    inflight_future: asyncio.Future = asyncio.get_running_loop().create_future()
    service._http_bridge_inflight_sessions[replay_key] = inflight_future

    request_key = proxy_module._HTTPBridgeSessionKey("request", "derived-key", None)
    try:
        waiter = asyncio.create_task(
            service._get_or_create_http_bridge_session(
                request_key,
                headers={"x-codex-turn-state": replay_turn_state},
                affinity=proxy_module._AffinityPolicy(key="derived-key"),
                api_key=None,
                request_model="gpt-5.4",
                idle_ttl_seconds=120.0,
                max_sessions=8,
            )
        )
        await asyncio.sleep(0)
        assert not waiter.done()
        inflight_future.set_result(expected_session)
        returned = await waiter
    finally:
        service._http_bridge_inflight_sessions.clear()

    assert returned is expected_session


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_generated_turn_state_fails_closed_without_local_alias(
    async_client,
    app_instance,
    monkeypatch,
):
    _install_bridge_settings_with_limits(
        monkeypatch,
        enabled=True,
        instance_id="instance-a",
        instance_ring=["instance-a", "instance-b"],
    )
    account_id = await _import_account(
        async_client,
        "acc_http_bridge_missing_alias",
        "http-bridge-missing-alias@example.com",
    )
    account = await _get_account(account_id)
    service = get_proxy_service_for_app(app_instance)

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

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)

    with pytest.raises(proxy_module.ProxyResponseError) as exc_info:
        await service._get_or_create_http_bridge_session(
            proxy_module._HTTPBridgeSessionKey("turn_state_header", "http_turn_missing_alias", None),
            headers={"x-codex-turn-state": "http_turn_missing_alias"},
            affinity=proxy_module._AffinityPolicy(
                key="http_turn_missing_alias",
                kind=proxy_module.StickySessionKind.CODEX_SESSION,
            ),
            api_key=None,
            request_model="gpt-5.1",
            idle_ttl_seconds=120.0,
            max_sessions=128,
        )

    exc = exc_info.value
    assert exc.status_code == 409
    assert exc.payload["error"].get("code") == "bridge_instance_mismatch"
