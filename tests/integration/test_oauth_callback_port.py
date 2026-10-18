from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import cast

import pytest

import app.modules.oauth.service as oauth_module
from app.modules.accounts.repository import AccountsRepository
from tests.integration.oauth_flow_support import (
    _oauth_state_token,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_internal_oauth_pending_reports_counts_and_ages_only(async_client):

    await oauth_module._OAUTH_STORE.reset()
    now = time.time()
    async with oauth_module._OAUTH_STORE.lock:
        for flow_id, started_at, expires_at, status in (
            ("pending-flow", now - 40, now + 600, "pending"),
            ("expired-flow", now - 1000, now - 1, "pending"),
            ("finished-flow", now - 50, now + 600, "success"),
        ):
            oauth_module._OAUTH_STORE.remember_flow_locked(
                oauth_module.OAuthState(
                    flow_id=flow_id,
                    status=status,
                    method="browser",
                    state_token=f"state-{flow_id}",
                    code_verifier=f"verifier-{flow_id}",
                    started_at=started_at,
                    expires_at=expires_at,
                )
            )

    response = await async_client.get("/internal/oauth/pending")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"pending", "ages_seconds"}
    assert body["pending"] == 1
    assert 39 <= body["ages_seconds"][0] <= 45
    assert "state-" not in response.text and "verifier-" not in response.text and "flow" not in response.text
    await oauth_module._OAUTH_STORE.reset()


@pytest.mark.asyncio
async def test_internal_oauth_pending_refuses_non_loopback_clients(app_instance):
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app_instance, client=("100.64.0.7", 51000))
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/internal/oauth/pending")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_abandoned_browser_flow_releases_the_callback_port(monkeypatch):

    import socket

    await oauth_module._OAUTH_STORE.reset()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setenv("AGENT_LB_OAUTH_CALLBACK_HOST", "127.0.0.1")
    monkeypatch.setenv("AGENT_LB_OAUTH_CALLBACK_PORT", str(port))
    monkeypatch.setattr(oauth_module, "_PENDING_BROWSER_OAUTH_FLOW_TTL_SECONDS", 0.1)
    monkeypatch.setattr(oauth_module, "_CALLBACK_PORT_RELEASE_GRACE_SECONDS", 0.1)
    service = oauth_module.OauthService(cast(AccountsRepository, SimpleNamespace()))

    await service._start_browser_flow("anthropic")
    assert oauth_module._OAUTH_STORE._callback_server is not None

    for _ in range(50):
        await asyncio.sleep(0.05)
        if oauth_module._OAUTH_STORE._callback_server is None:
            break
    assert oauth_module._OAUTH_STORE._callback_server is None
    with socket.socket() as rebind:
        rebind.bind(("127.0.0.1", port))
    await oauth_module._OAUTH_STORE.reset()


@pytest.mark.asyncio
async def test_failed_browser_flow_releases_the_callback_port(monkeypatch):
    import socket

    await oauth_module._OAUTH_STORE.reset()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setenv("AGENT_LB_OAUTH_CALLBACK_HOST", "127.0.0.1")
    monkeypatch.setenv("AGENT_LB_OAUTH_CALLBACK_PORT", str(port))
    service = oauth_module.OauthService(cast(AccountsRepository, SimpleNamespace()))

    started = await service._start_browser_flow("anthropic")
    assert started.authorization_url is not None
    state = _oauth_state_token(started.authorization_url)
    await service.manual_callback(f"http://localhost/?error=access_denied&state={state}", flow_id=started.flow_id)

    for _ in range(50):
        await asyncio.sleep(0.02)
        if oauth_module._OAUTH_STORE._callback_server is None:
            break
    assert oauth_module._OAUTH_STORE._callback_server is None
    with socket.socket() as rebind:
        rebind.bind(("127.0.0.1", port))
    await oauth_module._OAUTH_STORE.reset()


@pytest.mark.asyncio
async def test_concurrent_browser_flows_never_trust_an_unbound_callback_server(monkeypatch):

    await oauth_module._OAUTH_STORE.reset()
    release_bind = asyncio.Event()
    bind_attempts: list[object] = []

    class BusyPortServer:
        def __init__(self, *_, **__) -> None:
            pass

        async def start(self) -> None:
            bind_attempts.append(self)
            if len(bind_attempts) == 1:
                await release_bind.wait()
                raise OSError("address in use")

        async def stop(self) -> None:
            return None

    monkeypatch.setattr(oauth_module, "OAuthCallbackServer", BusyPortServer)
    service = oauth_module.OauthService(cast(AccountsRepository, SimpleNamespace()))

    first = asyncio.create_task(service._start_browser_flow("anthropic"))
    while not bind_attempts:
        await asyncio.sleep(0)
    assert oauth_module._OAUTH_STORE._callback_server is None
    second = asyncio.create_task(service._start_browser_flow("anthropic"))
    await asyncio.sleep(0.01)
    release_bind.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=1)

    assert len(bind_attempts) == 2
    assert oauth_module._OAUTH_STORE._callback_server is bind_attempts[1]
    await oauth_module._OAUTH_STORE.reset()


@pytest.mark.asyncio
async def test_cancelled_sign_in_request_still_registers_the_port_it_bound(monkeypatch):

    await oauth_module._OAUTH_STORE.reset()
    release_bind = asyncio.Event()
    bound: list[object] = []

    class SlowServer:
        def __init__(self, *_, **__) -> None:
            pass

        async def start(self) -> None:
            await release_bind.wait()
            bound.append(self)

        async def stop(self) -> None:
            return None

    monkeypatch.setattr(oauth_module, "OAuthCallbackServer", SlowServer)
    service = oauth_module.OauthService(cast(AccountsRepository, SimpleNamespace()))

    request = asyncio.create_task(service._start_browser_flow("anthropic"))
    await asyncio.sleep(0.01)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    release_bind.set()
    for _ in range(50):
        await asyncio.sleep(0.01)
        if bound and oauth_module._OAUTH_STORE._callback_server is not None:
            break

    assert len(bound) == 1
    assert oauth_module._OAUTH_STORE._callback_server is bound[0]
    await oauth_module._OAUTH_STORE.reset()


@pytest.mark.asyncio
async def test_bind_crash_after_request_cancelled_fails_the_flow(monkeypatch):
    await oauth_module._OAUTH_STORE.reset()
    release_bind = asyncio.Event()

    class CrashingServer:
        def __init__(self, *_, **__) -> None:
            pass

        async def start(self) -> None:
            await release_bind.wait()
            raise RuntimeError("listener setup failed")

        async def stop(self) -> None:
            return None

    monkeypatch.setattr(oauth_module, "OAuthCallbackServer", CrashingServer)
    service = oauth_module.OauthService(cast(AccountsRepository, SimpleNamespace()))

    request = asyncio.create_task(service._start_browser_flow("anthropic"))
    await asyncio.sleep(0.01)
    async with oauth_module._OAUTH_STORE.lock:
        (flow_id,) = list(oauth_module._OAUTH_STORE._flows)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    release_bind.set()
    await asyncio.sleep(0.2)

    async with oauth_module._OAUTH_STORE.lock:
        assert oauth_module._OAUTH_STORE.get_flow_locked(flow_id).status == "error"
    assert await oauth_module.pending_oauth_flow_ages() == []
    await oauth_module._OAUTH_STORE.reset()


@pytest.mark.asyncio
async def test_held_callback_port_counts_as_pending_until_released():

    await oauth_module._OAUTH_STORE.reset()
    now = time.time()

    class HeldServer:
        async def stop(self) -> None:
            return None

    held = cast(oauth_module.OAuthCallbackServer, HeldServer())
    async with oauth_module._OAUTH_STORE.lock:
        oauth_module._OAUTH_STORE.remember_flow_locked(
            oauth_module.OAuthState(
                flow_id="expired-flow",
                status="pending",
                method="browser",
                state_token="state-expired",
                started_at=now - 901,
                expires_at=now - 1,
            )
        )
        oauth_module._OAUTH_STORE._callback_server = held
        oauth_module._OAUTH_STORE._callback_server_bound_at = now - 901

    ages = await oauth_module.pending_oauth_flow_ages()
    assert len(ages) == 1 and ages[0] >= 900

    service = oauth_module.OauthService(cast(AccountsRepository, SimpleNamespace()))
    await service._stop_callback_server_if_idle()
    assert await oauth_module.pending_oauth_flow_ages() == []
    await oauth_module._OAUTH_STORE.reset()
