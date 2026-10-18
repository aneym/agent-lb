from __future__ import annotations
import asyncio
import pytest
import app.modules.proxy.service as proxy_module
from app.dependencies import get_proxy_service_for_app
from tests.integration.test_http_responses_bridge import (
    _cleanup_http_bridge_sessions,
    _install_proxy_settings,
    _make_app_settings,
    _make_dashboard_settings,
    _make_dummy_bridge_session,
    _wait_for_event,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_creates_different_session_keys_in_parallel(app_instance, monkeypatch):
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
    create_started_events = {
        "bridge-a": asyncio.Event(),
        "bridge-b": asyncio.Event(),
    }
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
        create_started.append(key.affinity_key)
        create_started_events[key.affinity_key].set()
        await _wait_for_event(release_create)
        return _make_dummy_bridge_session(key)

    monkeypatch.setattr(proxy_module.ProxyService, "_create_http_bridge_session", fake_create_http_bridge_session)

    key_one = proxy_module._HTTPBridgeSessionKey("request", "bridge-a", None)
    key_two = proxy_module._HTTPBridgeSessionKey("request", "bridge-b", None)

    try:
        first = asyncio.create_task(
            service._get_or_create_http_bridge_session(
                key_one,
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
                key_two,
                headers={},
                affinity=proxy_module._AffinityPolicy(),
                api_key=None,
                request_model="gpt-5.4",
                idle_ttl_seconds=120.0,
                max_sessions=8,
            )
        )
        await _wait_for_event(create_started_events["bridge-a"])
        await _wait_for_event(create_started_events["bridge-b"])
        assert key_one in service._http_bridge_inflight_sessions
        assert key_two in service._http_bridge_inflight_sessions

        release_create.set()
        session_one, session_two = await asyncio.gather(first, second)

        assert sorted(create_started) == ["bridge-a", "bridge-b"]
        assert session_one.key == key_one
        assert session_two.key == key_two
        assert service._http_bridge_sessions[key_one] is session_one
        assert service._http_bridge_sessions[key_two] is session_two
    finally:
        service._http_bridge_sessions.clear()
        service._http_bridge_inflight_sessions.clear()
        service._http_bridge_turn_state_index.clear()


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_singleflights_same_session_key_during_creation(app_instance, monkeypatch):
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
    create_started_event = asyncio.Event()
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
        create_started.append(key.affinity_key)
        create_started_event.set()
        await _wait_for_event(release_create)
        return _make_dummy_bridge_session(key)

    monkeypatch.setattr(proxy_module.ProxyService, "_create_http_bridge_session", fake_create_http_bridge_session)

    key = proxy_module._HTTPBridgeSessionKey("request", "bridge-singleflight", None)

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
        await _wait_for_event(create_started_event)
        assert create_started == ["bridge-singleflight"]
        assert key in service._http_bridge_inflight_sessions

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
        await asyncio.sleep(0)
        assert create_started == ["bridge-singleflight"]
        assert not second.done()

        release_create.set()
        session_one, session_two = await asyncio.gather(first, second)

        assert create_started == ["bridge-singleflight"]
        assert session_one is session_two
        assert service._http_bridge_sessions[key] is session_one
    finally:
        service._http_bridge_sessions.clear()
        service._http_bridge_inflight_sessions.clear()
        service._http_bridge_turn_state_index.clear()


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_waits_for_inflight_capacity_before_rate_limiting_other_keys(
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
            max_sessions=1,
            codex_idle_ttl_seconds=120.0,
            instance_id="instance-a",
            instance_ring=[],
        ),
        dashboard_settings=_make_dashboard_settings(),
    )

    first_create_started = asyncio.Event()
    release_first_create = asyncio.Event()
    create_attempts: list[str] = []

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
        create_attempts.append(key.affinity_key)
        if key.affinity_key == "bridge-capacity-a":
            first_create_started.set()
            await _wait_for_event(release_first_create)
            raise RuntimeError("first create failed")
        return _make_dummy_bridge_session(key)

    monkeypatch.setattr(proxy_module.ProxyService, "_create_http_bridge_session", fake_create_http_bridge_session)

    key_one = proxy_module._HTTPBridgeSessionKey("request", "bridge-capacity-a", None)
    key_two = proxy_module._HTTPBridgeSessionKey("request", "bridge-capacity-b", None)

    first = asyncio.create_task(
        service._get_or_create_http_bridge_session(
            key_one,
            headers={},
            affinity=proxy_module._AffinityPolicy(),
            api_key=None,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
            max_sessions=1,
        )
    )
    await _wait_for_event(first_create_started)

    second = asyncio.create_task(
        service._get_or_create_http_bridge_session(
            key_two,
            headers={},
            affinity=proxy_module._AffinityPolicy(),
            api_key=None,
            request_model="gpt-5.4",
            idle_ttl_seconds=120.0,
            max_sessions=1,
        )
    )
    await asyncio.sleep(0.01)
    assert not second.done()

    release_first_create.set()

    with pytest.raises(RuntimeError, match="first create failed"):
        await first
    created_session = await asyncio.wait_for(second, timeout=1.0)

    assert create_attempts == ["bridge-capacity-a", "bridge-capacity-b"]
    assert service._http_bridge_sessions[key_two] is created_session
    assert key_one not in service._http_bridge_inflight_sessions
    assert key_two not in service._http_bridge_inflight_sessions


@pytest.mark.asyncio
async def test_v1_responses_http_bridge_singleflight_follower_refreshes_session_model(app_instance, monkeypatch):
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
        session = _make_dummy_bridge_session(key)
        session.request_model = "gpt-5.1"
        return session

    monkeypatch.setattr(proxy_module.ProxyService, "_create_http_bridge_session", fake_create_http_bridge_session)

    key = proxy_module._HTTPBridgeSessionKey("session_header", "shared-session", None)

    try:
        creator = asyncio.create_task(
            service._get_or_create_http_bridge_session(
                key,
                headers={"session_id": "shared-session"},
                affinity=proxy_module._AffinityPolicy(
                    key="shared-session",
                    kind=proxy_module.StickySessionKind.CODEX_SESSION,
                ),
                api_key=None,
                request_model="gpt-5.1",
                idle_ttl_seconds=120.0,
                max_sessions=8,
            )
        )
        await _wait_for_event(create_started)
        follower = asyncio.create_task(
            service._get_or_create_http_bridge_session(
                key,
                headers={"session_id": "shared-session"},
                affinity=proxy_module._AffinityPolicy(
                    key="shared-session",
                    kind=proxy_module.StickySessionKind.CODEX_SESSION,
                ),
                api_key=None,
                request_model="gpt-5.4",
                idle_ttl_seconds=120.0,
                max_sessions=8,
            )
        )
        release_create.set()
        created_session, follower_session = await asyncio.gather(creator, follower)

        assert created_session is follower_session
        assert follower_session.request_model == "gpt-5.4"
    finally:
        service._http_bridge_sessions.clear()
        service._http_bridge_inflight_sessions.clear()
        service._http_bridge_turn_state_index.clear()
