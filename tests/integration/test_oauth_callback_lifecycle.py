from __future__ import annotations

import asyncio
import time
from typing import cast

import pytest

import app.modules.oauth.service as oauth_module
from app.core.clients.oauth import DeviceCode
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_oauth_start_with_existing_account_marks_success(async_client):
    await oauth_module._OAUTH_STORE.reset()

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_existing",
        email="existing@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(account)

    start = await async_client.post("/api/oauth/start", json={})
    assert start.status_code == 200
    assert start.json()["method"] == "browser"

    status = await async_client.get("/api/oauth/status")
    assert status.status_code == 200
    assert status.json()["status"] == "success"


@pytest.mark.asyncio
async def test_oauth_start_with_existing_account_clears_stale_flows(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    async def fake_callback_server_start(self) -> None:
        return None

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)

    stale_start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})
    assert stale_start.status_code == 200
    stale_payload = stale_start.json()
    assert stale_payload["flowId"]

    async with oauth_module._OAUTH_STORE.lock:
        assert oauth_module._OAUTH_STORE._flows
        assert oauth_module._OAUTH_STORE._state_token_index

    encryptor = TokenEncryptor()
    account = Account(
        id="acc_existing_after_stale_flow",
        email="existing-after-stale-flow@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(account)

    start = await async_client.post("/api/oauth/start", json={})
    assert start.status_code == 200
    assert start.json()["method"] == "browser"

    status = await async_client.get("/api/oauth/status")
    assert status.status_code == 200
    assert status.json() == {"status": "success", "errorMessage": None}

    async with oauth_module._OAUTH_STORE.lock:
        assert oauth_module._OAUTH_STORE._flows == {}
        assert oauth_module._OAUTH_STORE._state_token_index == {}


@pytest.mark.asyncio
async def test_terminal_oauth_flows_are_bounded_outside_full_reset():
    await oauth_module._OAUTH_STORE.reset()

    retained_limit = oauth_module._MAX_RETAINED_TERMINAL_OAUTH_FLOWS

    async with oauth_module._OAUTH_STORE.lock:
        for index in range(retained_limit + 2):
            flow = oauth_module.OAuthState(
                flow_id=f"flow-{index}",
                status="pending",
                method="browser",
                state_token=f"state-{index}",
                code_verifier=f"verifier-{index}",
            )
            oauth_module._OAUTH_STORE.remember_flow_locked(flow)
            oauth_module._OAUTH_STORE.set_flow_status_locked(
                flow,
                status="error",
                error_message=f"failure-{index}",
            )

        assert len(oauth_module._OAUTH_STORE._flows) == retained_limit
        assert "flow-0" not in oauth_module._OAUTH_STORE._flows
        assert "flow-1" not in oauth_module._OAUTH_STORE._flows
        assert "state-0" not in oauth_module._OAUTH_STORE._state_token_index
        assert "state-1" not in oauth_module._OAUTH_STORE._state_token_index
        assert f"flow-{retained_limit + 1}" in oauth_module._OAUTH_STORE._flows
        assert oauth_module._OAUTH_STORE.state.error_message == f"failure-{retained_limit + 1}"


@pytest.mark.asyncio
async def test_expired_pending_browser_oauth_flows_are_pruned():
    await oauth_module._OAUTH_STORE.reset()

    now = time.time()
    async with oauth_module._OAUTH_STORE.lock:
        expired = oauth_module.OAuthState(
            flow_id="expired-flow",
            status="pending",
            method="browser",
            state_token="expired-state",
            code_verifier="expired-verifier",
            expires_at=now - 1,
        )
        active = oauth_module.OAuthState(
            flow_id="active-flow",
            status="pending",
            method="browser",
            state_token="active-state",
            code_verifier="active-verifier",
            expires_at=now + oauth_module._PENDING_BROWSER_OAUTH_FLOW_TTL_SECONDS,
        )
        oauth_module._OAUTH_STORE.remember_flow_locked(expired)
        oauth_module._OAUTH_STORE.remember_flow_locked(active)

        assert oauth_module._OAUTH_STORE.has_pending_browser_flows_locked()
        assert "expired-flow" not in oauth_module._OAUTH_STORE._flows
        assert "expired-state" not in oauth_module._OAUTH_STORE._state_token_index
        assert oauth_module._OAUTH_STORE.state.flow_id == "active-flow"


@pytest.mark.asyncio
async def test_only_expired_pending_browser_flow_no_longer_keeps_callback_server_alive():
    await oauth_module._OAUTH_STORE.reset()

    async with oauth_module._OAUTH_STORE.lock:
        flow = oauth_module.OAuthState(
            flow_id="expired-flow",
            status="pending",
            method="browser",
            state_token="expired-state",
            code_verifier="expired-verifier",
            expires_at=time.time() - 1,
        )
        oauth_module._OAUTH_STORE.remember_flow_locked(flow)

        assert not oauth_module._OAUTH_STORE.has_pending_browser_flows_locked()
        assert oauth_module._OAUTH_STORE._flows == {}
        assert oauth_module._OAUTH_STORE.state.status == "idle"


@pytest.mark.asyncio
async def test_callback_server_remains_reserved_until_stop_completes():
    await oauth_module._OAUTH_STORE.reset()
    stop_started = asyncio.Event()
    release_stop = asyncio.Event()

    class FakeCallbackServer:
        async def stop(self) -> None:
            stop_started.set()
            await release_stop.wait()

    fake_server = FakeCallbackServer()
    async with SessionLocal() as session:
        service = oauth_module.OauthService(AccountsRepository(session))
        async with oauth_module._OAUTH_STORE.lock:
            oauth_module._OAUTH_STORE._callback_server = cast(oauth_module.OAuthCallbackServer, fake_server)

        stop_task = asyncio.create_task(service._stop_callback_server_if_idle())
        await stop_started.wait()
        assert oauth_module._OAUTH_STORE._callback_server is fake_server

        release_stop.set()
        await stop_task
        assert oauth_module._OAUTH_STORE._callback_server is None


@pytest.mark.asyncio
async def test_oauth_start_falls_back_to_device_on_os_error(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    async def fake_browser_flow(self):
        raise OSError("no port")

    async def fake_device_code(**_):
        return DeviceCode(
            verification_url="https://auth.openai.com/codex/device",
            user_code="ABCD-EFGH",
            device_auth_id="dev_fallback",
            interval_seconds=1,
            expires_in_seconds=30,
        )

    monkeypatch.setattr(oauth_module.OauthService, "_start_browser_flow", fake_browser_flow)
    monkeypatch.setattr(oauth_module, "request_device_code", fake_device_code)

    start = await async_client.post("/api/oauth/start", json={})
    assert start.status_code == 200
    payload = start.json()
    assert payload["method"] == "device"
    assert payload["deviceAuthId"] == "dev_fallback"


@pytest.mark.asyncio
async def test_callback_server_idle_stop_releases_store_lock_before_cleanup():
    await oauth_module._OAUTH_STORE.reset()
    async with SessionLocal() as session:
        service = oauth_module.OauthService(AccountsRepository(session))

        class ObservingCallbackServer:
            async def stop(self) -> None:
                assert not oauth_module._OAUTH_STORE.lock.locked()

        async with oauth_module._OAUTH_STORE.lock:
            flow = oauth_module.OAuthState(
                flow_id="finished-browser-flow",
                status="success",
                method="browser",
                state_token="finished-state",
                code_verifier="finished-verifier",
            )
            oauth_module._OAUTH_STORE.remember_flow_locked(flow)
            oauth_module._OAUTH_STORE.set_flow_status_locked(flow, status="success", error_message=None)
            oauth_module._OAUTH_STORE._callback_server = cast(
                oauth_module.OAuthCallbackServer,
                ObservingCallbackServer(),
            )

        await service._stop_callback_server_if_idle()


@pytest.mark.asyncio
async def test_existing_account_cleanup_releases_store_lock_before_callback_server_stop():
    await oauth_module._OAUTH_STORE.reset()

    class ExistingAccountRepo:
        async def list_accounts(self):
            return [object()]

    class ObservingCallbackServer:
        async def stop(self) -> None:
            assert not oauth_module._OAUTH_STORE.lock.locked()

    service = oauth_module.OauthService(cast(AccountsRepository, ExistingAccountRepo()))
    async with oauth_module._OAUTH_STORE.lock:
        flow = oauth_module.OAuthState(
            flow_id="pending-browser-flow",
            status="pending",
            method="browser",
            state_token="pending-state",
            code_verifier="pending-verifier",
        )
        oauth_module._OAUTH_STORE.remember_flow_locked(flow)
        oauth_module._OAUTH_STORE._callback_server = cast(
            oauth_module.OAuthCallbackServer,
            ObservingCallbackServer(),
        )

    response = await service.start_oauth(oauth_module.OauthStartRequest())

    assert response.method == "browser"


@pytest.mark.asyncio
async def test_new_browser_flow_waits_for_stopping_callback_server_before_reusing_slot(monkeypatch):
    await oauth_module._OAUTH_STORE.reset()
    stop_started = asyncio.Event()
    release_stop = asyncio.Event()
    started_servers: list[object] = []

    class StoppingCallbackServer:
        async def stop(self) -> None:
            stop_started.set()
            await release_stop.wait()

    class ReplacementCallbackServer:
        def __init__(self, *_, **__) -> None:
            self.started = False

        async def start(self) -> None:
            self.started = True
            started_servers.append(self)

        async def stop(self) -> None:
            return None

    async with SessionLocal() as session:
        service = oauth_module.OauthService(AccountsRepository(session))
        stopping_server = StoppingCallbackServer()
        async with oauth_module._OAUTH_STORE.lock:
            oauth_module._OAUTH_STORE._callback_server = cast(oauth_module.OAuthCallbackServer, stopping_server)

        monkeypatch.setattr(oauth_module, "OAuthCallbackServer", ReplacementCallbackServer)
        stop_task = asyncio.create_task(service._stop_callback_server_if_idle())
        await stop_started.wait()

        start_task = asyncio.create_task(service._start_browser_flow())
        await asyncio.sleep(0)
        release_stop.set()

        response = await asyncio.wait_for(start_task, timeout=1)
        await stop_task

        assert response.method == "browser"
        assert len(started_servers) == 1
        async with oauth_module._OAUTH_STORE.lock:
            assert oauth_module._OAUTH_STORE._callback_server is started_servers[0]
