from __future__ import annotations
import asyncio
from types import SimpleNamespace
from typing import Any, cast
import pytest
import app.modules.proxy.service as proxy_module
from app.db.models import Account, AccountStatus, DashboardSettings, RequestLog
from app.dependencies import get_proxy_service_for_app
from tests.integration.test_http_responses_bridge import (
    _TEST_SYNC_TIMEOUT_SECONDS,
    _cleanup_http_bridge_sessions,
    _import_account,
    _install_proxy_settings,
    _make_api_key_data,
    _make_app_settings,
    _make_dashboard_settings,
    _make_dummy_bridge_session,
    _wait_for_event,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_singleflight_follower_replaces_session_when_account_is_no_longer_assigned(
    async_client, app_instance, monkeypatch
):
    service = get_proxy_service_for_app(app_instance)
    service._http_bridge_sessions.clear()
    service._http_bridge_inflight_sessions.clear()
    service._http_bridge_turn_state_index.clear()

    _install_proxy_settings(
        monkeypatch,
        app_settings=_make_app_settings(
            enabled=True,
            max_sessions=8,
            admission_wait_timeout_seconds=1.0,
            codex_idle_ttl_seconds=120.0,
            instance_id="instance-a",
            instance_ring=[],
        ),
        dashboard_settings=_make_dashboard_settings(),
    )

    create_started = asyncio.Event()
    release_create = asyncio.Event()
    create_calls: list[list[str]] = []
    durable_claims: list[tuple[str, bool]] = []
    stale_account_id = await _import_account(
        async_client,
        "acc_http_bridge_stale",
        "http-bridge-stale@example.com",
    )
    fresh_account_id = await _import_account(
        async_client,
        "acc_http_bridge_fresh",
        "http-bridge-fresh@example.com",
    )

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
        create_calls.append(list(api_key.assigned_account_ids if api_key is not None else []))
        if len(create_calls) == 1:
            create_started.set()
            await _wait_for_event(release_create)
            session = cast(proxy_module._HTTPBridgeSession, _make_dummy_bridge_session(key))
            cast(Any, session).account = SimpleNamespace(id=stale_account_id, status=AccountStatus.ACTIVE)
            session.queued_request_count = 1
            session.upstream_control.retire_after_drain = True
            return session
        session = cast(proxy_module._HTTPBridgeSession, _make_dummy_bridge_session(key))
        cast(Any, session).account = SimpleNamespace(id=fresh_account_id, status=AccountStatus.ACTIVE)
        return session

    monkeypatch.setattr(proxy_module.ProxyService, "_create_http_bridge_session", fake_create_http_bridge_session)

    async def fake_claim_durable_http_bridge_session(
        self,
        session,
        *,
        allow_takeover,
        force_owner_epoch_advance=False,
    ):
        del self, allow_takeover
        durable_claims.append((session.account.id, force_owner_epoch_advance))
        session.durable_session_id = "durable-session"
        session.durable_owner_epoch = 2 if force_owner_epoch_advance else 1

    monkeypatch.setattr(
        proxy_module.ProxyService,
        "_claim_durable_http_bridge_session",
        fake_claim_durable_http_bridge_session,
    )

    session_header = f"shared-session-{stale_account_id}"
    key = proxy_module._HTTPBridgeSessionKey("session_header", session_header, "key-assignments")
    stale_api_key = _make_api_key_data(key_id="key-assignments", assigned_account_ids=[stale_account_id])
    refreshed_api_key = _make_api_key_data(key_id="key-assignments", assigned_account_ids=[fresh_account_id])

    try:
        creator = asyncio.create_task(
            service._get_or_create_http_bridge_session(
                key,
                headers={"session_id": session_header},
                affinity=proxy_module._AffinityPolicy(
                    key=session_header,
                    kind=proxy_module.StickySessionKind.CODEX_SESSION,
                ),
                api_key=stale_api_key,
                request_model="gpt-5.1",
                idle_ttl_seconds=120.0,
                max_sessions=8,
            )
        )
        await _wait_for_event(create_started)
        follower = asyncio.create_task(
            service._get_or_create_http_bridge_session(
                key,
                headers={"session_id": session_header},
                affinity=proxy_module._AffinityPolicy(
                    key=session_header,
                    kind=proxy_module.StickySessionKind.CODEX_SESSION,
                ),
                api_key=refreshed_api_key,
                request_model="gpt-5.4",
                idle_ttl_seconds=120.0,
                max_sessions=8,
            )
        )
        release_create.set()
        created_session, follower_session = await asyncio.gather(creator, follower)

        assert created_session is not follower_session
        assert created_session.account.id == stale_account_id
        assert follower_session.account.id == fresh_account_id
        assert service._http_bridge_sessions[key] is follower_session
        assert create_calls == [[stale_account_id], [fresh_account_id]]
        assert durable_claims == [(stale_account_id, False), (fresh_account_id, True)]
    finally:
        service._http_bridge_sessions.clear()
        service._http_bridge_inflight_sessions.clear()
        service._http_bridge_turn_state_index.clear()


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_singleflights_stale_session_replacement(app_instance, monkeypatch):
    service = get_proxy_service_for_app(app_instance)
    service._http_bridge_sessions.clear()
    service._http_bridge_inflight_sessions.clear()
    service._http_bridge_turn_state_index.clear()

    _install_proxy_settings(
        monkeypatch,
        app_settings=_make_app_settings(
            enabled=True,
            max_sessions=8,
            admission_wait_timeout_seconds=1.0,
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
        await asyncio.sleep(0.2)
        return _make_dummy_bridge_session(key)

    monkeypatch.setattr(proxy_module.ProxyService, "_create_http_bridge_session", fake_create_http_bridge_session)

    key = proxy_module._HTTPBridgeSessionKey("request", "bridge-stale-replace", None)
    stale_session = cast(proxy_module._HTTPBridgeSession, _make_dummy_bridge_session(key))
    stale_session.closed = True
    service._http_bridge_sessions[key] = stale_session

    try:
        first = asyncio.create_task(
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
        second = asyncio.create_task(
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
        session_one, session_two = await asyncio.gather(first, second)

        assert create_started == ["bridge-stale-replace"]
        assert session_one is session_two
        assert service._http_bridge_sessions[key] is session_one
    finally:
        service._http_bridge_sessions.clear()
        service._http_bridge_inflight_sessions.clear()
        service._http_bridge_turn_state_index.clear()


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_cleans_up_cancelled_singleflight_creator(app_instance, monkeypatch):
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

    first_create_started = asyncio.Event()
    create_attempts = 0

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
        nonlocal create_attempts
        create_attempts += 1
        if create_attempts == 1:
            first_create_started.set()
            await _wait_for_event(asyncio.Event())
        return _make_dummy_bridge_session(key)

    monkeypatch.setattr(proxy_module.ProxyService, "_create_http_bridge_session", fake_create_http_bridge_session)

    key = proxy_module._HTTPBridgeSessionKey("request", "bridge-cancelled-create", None)

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
    await _wait_for_event(first_create_started)
    creator.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(creator, timeout=_TEST_SYNC_TIMEOUT_SECONDS)

    replacement = await asyncio.wait_for(
        service._get_or_create_http_bridge_session(
            key,
            headers={},
            affinity=proxy_module._AffinityPolicy(),
            api_key=None,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
            max_sessions=8,
        ),
        timeout=1.0,
    )

    assert create_attempts == 2
    assert service._http_bridge_sessions[key] is replacement
    assert key not in service._http_bridge_inflight_sessions


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_cleans_up_cancelled_singleflight_creator_after_create(
    app_instance, monkeypatch
):
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

    create_finished = asyncio.Event()
    allow_return = asyncio.Event()
    create_attempts = 0

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
        nonlocal create_attempts
        create_attempts += 1
        if create_attempts == 1:
            create_finished.set()
            await _wait_for_event(allow_return)
        return _make_dummy_bridge_session(key)

    monkeypatch.setattr(proxy_module.ProxyService, "_create_http_bridge_session", fake_create_http_bridge_session)

    key = proxy_module._HTTPBridgeSessionKey("request", "bridge-cancelled-after-create", None)
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
    await _wait_for_event(create_finished)
    async with service._http_bridge_lock:
        allow_return.set()
        await asyncio.sleep(0)
        creator.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(creator, timeout=_TEST_SYNC_TIMEOUT_SECONDS)

    replacement = await asyncio.wait_for(
        service._get_or_create_http_bridge_session(
            key,
            headers={},
            affinity=proxy_module._AffinityPolicy(),
            api_key=None,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
            max_sessions=8,
        ),
        timeout=1.0,
    )

    assert create_attempts == 2
    assert service._http_bridge_sessions[key] is replacement
    assert key not in service._http_bridge_inflight_sessions
