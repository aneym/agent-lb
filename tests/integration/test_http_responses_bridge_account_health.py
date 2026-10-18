from __future__ import annotations
import contextlib
import json
from collections.abc import AsyncGenerator
import pytest
from httpx import ASGITransport, AsyncClient
import app.modules.proxy.service as proxy_module
from app.db.models import Account, AccountStatus, DashboardSettings, RequestLog
from tests.integration.test_http_responses_bridge import (
    _FakeBridgeUpstreamWebSocket,
    _cleanup_http_bridge_sessions,
    _get_account,
    _import_account,
    _install_bridge_settings,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("rejection", ["upstream_403", "refresh_needs_reauth"])
async def test_ccgpt_turn_moves_to_another_account_when_its_account_rejects_it(async_client, monkeypatch, rejection):

    _install_bridge_settings(monkeypatch, enabled=True)
    await _import_account(async_client, "acc_ccgpt_reject_a", "ccgpt-reject-a@example.com")
    await _import_account(async_client, "acc_ccgpt_reject_b", "ccgpt-reject-b@example.com")
    fake_upstream = _FakeBridgeUpstreamWebSocket()
    attempts: list[str] = []

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        attempts.append(target.chatgpt_account_id)
        if rejection == "refresh_needs_reauth" and len(attempts) == 1:
            raise proxy_module.RefreshError("refresh_token_invalidated", "reauth required", True)
        return target

    async def fake_connect_responses_websocket(
        headers, access_token, account_id_header, *, base_url=None, session=None
    ):
        del headers, access_token, base_url, session
        if rejection == "upstream_403" and account_id_header == attempts[0]:
            raise proxy_module.ProxyResponseError(403, proxy_module.openai_error("forbidden", "Forbidden"))
        return fake_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "sol-latest",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "hello"}],
            "metadata": {"user_id": json.dumps({"session_id": "ccgpt-reject-session"})},
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["content"][0]["text"] == "OK"
    assert len(attempts) == 2 and attempts[0] != attempts[1]


@pytest.mark.asyncio
async def test_ccgpt_session_survives_its_account_needing_reauth_across_a_blue_green_swap(
    async_client, app_instance, monkeypatch
):

    from app.main import create_app

    _install_bridge_settings(monkeypatch, enabled=True)
    account_a = await _import_account(async_client, "acc_ccgpt_reauth_a", "ccgpt-reauth-a@example.com")
    account_b = await _import_account(async_client, "acc_ccgpt_reauth_b", "ccgpt-reauth-b@example.com")
    disconnected: set[str] = set()
    attempts: list[str] = []

    async def fake_ensure_fresh_with_budget(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        attempts.append(target.id)
        if target.id in disconnected:
            raise proxy_module.RefreshError("refresh_token_invalidated", "reauth required", True)
        return target

    async def fake_connect_responses_websocket(
        headers, access_token, account_id_header, *, base_url=None, session=None
    ):
        del headers, access_token, account_id_header, base_url, session
        return _FakeBridgeUpstreamWebSocket()

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", fake_connect_responses_websocket)

    history: list[dict[str, str]] = []

    async def turn(client: AsyncClient, text: str) -> None:
        history.append({"role": "user", "content": text})
        response = await client.post(
            "/v1/messages",
            json={
                "model": "sol-latest",
                "max_tokens": 1024,
                "messages": list(history),
                "metadata": {"user_id": json.dumps({"session_id": "ccgpt-reauth-session"})},
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["content"][0]["text"] == "OK"
        history.append({"role": "assistant", "content": "OK"})

    @contextlib.asynccontextmanager
    async def standby() -> AsyncGenerator[AsyncClient, None]:
        fresh = create_app()
        async with fresh.router.lifespan_context(fresh):
            async with AsyncClient(transport=ASGITransport(app=fresh), base_url="http://testserver") as client:
                yield client

    await turn(async_client, "hello")
    pinned = attempts[-1]
    other = account_b if pinned == account_a else account_a

    disconnected.add(pinned)
    async with standby() as client:
        await turn(client, "and now?")
    assert attempts[-1] == other
    assert (await _get_account(pinned)).status == AccountStatus.REAUTH_REQUIRED

    attempts.clear()
    async with standby() as client:
        await turn(client, "still there?")
    assert attempts == [other]
