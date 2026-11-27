from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import pytest
import pytest_asyncio
from aiohttp import web
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.core.clients.oauth import pkce_challenge
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.oauth_models import OAuthFlow
from app.db.session import SessionLocal
from app.modules.oauth.service import stop_oauth_flows
from tests.floor.oauth_flow_support import _oauth_state_token, encode_jwt

pytestmark = pytest.mark.integration

_EMAIL = "target@example.invalid"
_ACCOUNT_ID = "expected-openai"
_UPSTREAM_ID = "vendor-target"
_WORKSPACE = "workspace-target"
_ANTHROPIC_ACCOUNT_ID = "expected-anthropic"


@dataclass
class OAuthVendor:
    browser_calls: int = 0
    device_calls: int = 0
    probe_calls: int = 0
    committed_before_probe: bool = False
    authorized: bool = False
    workspace: str | None = _WORKSPACE
    authorized_browser: asyncio.Event = field(default_factory=asyncio.Event)
    allow_browser_reply: asyncio.Event = field(default_factory=asyncio.Event)
    code_challenge: str | None = None

    def credentials(self) -> web.Response:
        return web.json_response(
            {
                "access_token": "fixture-access",
                "refresh_token": "fixture-refresh",
                "id_token": encode_jwt(
                    {
                        "email": _EMAIL,
                        "https://api.openai.com/auth": {
                            "chatgpt_account_id": _UPSTREAM_ID,
                            "workspace_id": self.workspace,
                        },
                    }
                ),
            }
        )

    async def browser_exchange(self, request: web.Request) -> web.Response:
        self.browser_calls += 1
        form = await request.post()
        if form.get("code") != "browser-code" or pkce_challenge(str(form.get("code_verifier"))) != self.code_challenge:
            return web.json_response({"error": "invalid_grant"}, status=400)
        self.authorized_browser.set()
        await self.allow_browser_reply.wait()
        return self.credentials()

    async def device_start(self, _request: web.Request) -> web.Response:
        return web.json_response(
            {"device_auth_id": "fixture-device", "user_code": "TESTCODE", "interval": 1, "expires_in": 7200}
        )

    async def anthropic_exchange(self, request: web.Request) -> web.Response:
        payload = await request.json()
        if payload.get("code") != "anthropic-code" or not payload.get("state"):
            return web.json_response({"error": "invalid_grant"}, status=400)
        return web.json_response(
            {
                "access_token": "fixture-access",
                "refresh_token": "fixture-refresh",
                "account": {"uuid": _UPSTREAM_ID, "email_address": _EMAIL},
            }
        )

    async def device_exchange(self, request: web.Request) -> web.Response:
        self.device_calls += 1
        if await request.json() != {"device_auth_id": "fixture-device", "user_code": "TESTCODE"}:
            return web.json_response({"error": "invalid_grant"}, status=400)
        if not self.authorized:
            return web.json_response({}, status=404)
        return self.credentials()

    async def probe(self, _request: web.Request) -> web.Response:
        self.probe_calls += 1
        account_id = _ANTHROPIC_ACCOUNT_ID if _request.method == "POST" else _ACCOUNT_ID
        async with SessionLocal() as session:
            account = await session.get(Account, account_id)
            self.committed_before_probe = bool(
                account and TokenEncryptor().decrypt(account.refresh_token_encrypted) == "fixture-refresh"
            )
        return web.json_response({}, status=503)


@pytest_asyncio.fixture
async def oauth_vendor(monkeypatch):
    await stop_oauth_flows()
    vendor = OAuthVendor()
    upstream = web.Application()
    upstream.add_routes(
        [
            web.post("/oauth/token", vendor.browser_exchange),
            web.post("/v1/oauth/token", vendor.anthropic_exchange),
            web.post("/v1/messages", vendor.probe),
            web.post("/api/accounts/deviceauth/usercode", vendor.device_start),
            web.post("/api/accounts/deviceauth/token", vendor.device_exchange),
            web.get("/backend-api/wham/usage", vendor.probe),
        ]
    )
    runner = web.AppRunner(upstream)
    await runner.setup()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    await web.SockSite(runner, listener).start()
    monkeypatch.setenv("AGENT_LB_AUTH_BASE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("AGENT_LB_UPSTREAM_BASE_URL", f"http://127.0.0.1:{port}/backend-api")
    monkeypatch.setenv("AGENT_LB_ANTHROPIC_OAUTH_TOKEN_URL", f"http://127.0.0.1:{port}/v1/oauth/token")
    monkeypatch.setenv("AGENT_LB_ANTHROPIC_UPSTREAM_BASE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("AGENT_LB_OAUTH_CALLBACK_HOST", "127.0.0.1")
    monkeypatch.setenv("AGENT_LB_OAUTH_CALLBACK_PORT", "0")
    get_settings.cache_clear()
    try:
        yield vendor
    finally:
        vendor.allow_browser_reply.set()
        await stop_oauth_flows()
        await runner.cleanup()
        get_settings.cache_clear()


async def _account() -> None:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        for account_id, workspace_id in ((_ACCOUNT_ID, _WORKSPACE), ("same-email-other-workspace", "other")):
            session.add(
                Account(
                    id=account_id,
                    provider="openai",
                    email=_EMAIL,
                    chatgpt_account_id=_UPSTREAM_ID,
                    workspace_id=workspace_id,
                    status=AccountStatus.REAUTH_REQUIRED,
                    plan_type="plus",
                    access_token_encrypted=encryptor.encrypt("old-access"),
                    refresh_token_encrypted=encryptor.encrypt("old-refresh"),
                    last_refresh=utcnow(),
                )
            )
        await session.commit()


async def _status(client: AsyncClient, flow_id: str) -> dict:
    response = await client.get("/api/oauth/status", params={"flowId": flow_id})
    response.raise_for_status()
    return response.json()


async def _refresh_marker(account_id: str) -> str:
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        return TokenEncryptor().decrypt(account.refresh_token_encrypted)


@pytest.mark.asyncio
async def test_browser_callback_resumes_after_restart_and_claims_code_once(oauth_vendor, app_instance):
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        async with app_instance.router.lifespan_context(app_instance):
            await _account()
            response = await client.post(
                "/api/oauth/start", json={"forceMethod": "browser", "expectedAccountId": _ACCOUNT_ID}
            )
            response.raise_for_status()
            browser = response.json()
            state = _oauth_state_token(browser["authorizationUrl"])
            oauth_vendor.code_challenge = parse_qs(urlparse(browser["authorizationUrl"]).query)["code_challenge"][0]
            async with SessionLocal() as session:
                stored = await session.get(OAuthFlow, browser["flowId"])
                assert stored is not None
                assert stored.expires_at - stored.started_at == 3600
                assert stored.secrets_encrypted is not None
                assert state.encode() not in stored.secrets_encrypted
        await stop_oauth_flows()
        async with app_instance.router.lifespan_context(app_instance):
            assert (await _status(client, browser["flowId"]))["status"] == "pending"
            body = {"flowId": browser["flowId"], "callbackUrl": f"browser-code#{state}"}
            completion = asyncio.create_task(client.post("/api/oauth/manual-callback", json=body))
            try:
                await asyncio.wait_for(oauth_vendor.authorized_browser.wait(), timeout=5)
                duplicate = await client.post("/api/oauth/manual-callback", json=body)
                assert duplicate.json()["status"] == "pending"
                assert (await _status(client, browser["flowId"]))["status"] == "pending"
            finally:
                oauth_vendor.allow_browser_reply.set()
                complete = await completion
            assert complete.json() == {"status": "success", "errorMessage": None, "probeStatusCode": 503}
            duplicate = await client.post("/api/oauth/manual-callback", json=body)
            assert duplicate.json() == complete.json()
            assert await _status(client, browser["flowId"]) == complete.json()
            assert oauth_vendor.browser_calls == oauth_vendor.probe_calls == 1
            assert oauth_vendor.committed_before_probe
            assert await _refresh_marker(_ACCOUNT_ID) == "fixture-refresh"
            assert await _refresh_marker("same-email-other-workspace") == "old-refresh"
            async with SessionLocal() as session:
                stored = await session.get(OAuthFlow, browser["flowId"])
                assert stored is not None and stored.secrets_encrypted is None


@pytest.mark.asyncio
@pytest.mark.parametrize("workspace", [_WORKSPACE, None], ids=["matching-workspace", "missing-workspace"])
async def test_device_auto_poll_resumes_with_original_binding(oauth_vendor, app_instance, workspace):
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        async with app_instance.router.lifespan_context(app_instance):
            await _account()
            response = await client.post(
                "/api/oauth/start", json={"forceMethod": "device", "expectedAccountId": _ACCOUNT_ID}
            )
            response.raise_for_status()
            started = response.json()
            assert started["expectedAccountId"] == _ACCOUNT_ID
            assert started["expiresInSeconds"] == 3600
            async with asyncio.timeout(5):
                while oauth_vendor.device_calls == 0:
                    await asyncio.sleep(0.01)
        await stop_oauth_flows()
        oauth_vendor.workspace = workspace
        oauth_vendor.authorized = True
        async with app_instance.router.lifespan_context(app_instance):
            async with asyncio.timeout(5):
                while (result := await _status(client, started["flowId"]))["status"] == "pending":
                    await asyncio.sleep(0.01)
            assert oauth_vendor.device_calls == 2
            if workspace is None:
                assert result["status"] == "error"
                assert "does not match" in result["errorMessage"]
                assert oauth_vendor.probe_calls == 0
                assert await _refresh_marker(_ACCOUNT_ID) == "old-refresh"
            else:
                assert result == {"status": "success", "errorMessage": None, "probeStatusCode": 503}
                assert oauth_vendor.probe_calls == 1 and oauth_vendor.committed_before_probe
                assert await _refresh_marker(_ACCOUNT_ID) == "fixture-refresh"
            assert await _refresh_marker("same-email-other-workspace") == "old-refresh"


@pytest.mark.asyncio
async def test_anthropic_bound_manual_fragment_saves_before_probe(oauth_vendor, async_client):
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        session.add(
            Account(
                id=_ANTHROPIC_ACCOUNT_ID,
                provider="anthropic",
                email=_EMAIL,
                chatgpt_account_id=_EMAIL,
                status=AccountStatus.REAUTH_REQUIRED,
                plan_type="max",
                access_token_encrypted=encryptor.encrypt("old-access"),
                refresh_token_encrypted=encryptor.encrypt("old-refresh"),
                last_refresh=utcnow(),
            )
        )
        await session.commit()
    started = await async_client.post(
        "/api/oauth/start",
        json={"forceMethod": "browser", "provider": "anthropic", "expectedAccountId": _ANTHROPIC_ACCOUNT_ID},
    )
    started.raise_for_status()
    flow = started.json()
    state = _oauth_state_token(flow["authorizationUrl"])
    completed = await async_client.post(
        "/api/oauth/manual-callback", json={"flowId": flow["flowId"], "callbackUrl": f"anthropic-code#{state}"}
    )
    assert completed.json() == {"status": "success", "errorMessage": None, "probeStatusCode": 503}
    assert oauth_vendor.probe_calls == 1 and oauth_vendor.committed_before_probe
    assert await _refresh_marker(_ANTHROPIC_ACCOUNT_ID) == "fixture-refresh"


@pytest.mark.asyncio
async def test_invalid_state_and_expired_callback_do_not_consume_live_flow(oauth_vendor, async_client):
    first = (await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})).json()
    second = (await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})).json()
    first_state = _oauth_state_token(first["authorizationUrl"])
    second_state = _oauth_state_token(second["authorizationUrl"])

    denied = await async_client.post(
        "/api/oauth/manual-callback",
        json={"flowId": first["flowId"], "callbackUrl": f"http://localhost/?error=access_denied&state={second_state}"},
    )
    assert denied.json()["status"] == "error"
    assert (await _status(async_client, first["flowId"]))["status"] == "pending"
    assert (await _status(async_client, second["flowId"]))["status"] == "pending"

    async with SessionLocal() as session:
        await session.execute(update(OAuthFlow).where(OAuthFlow.id == first["flowId"]).values(expires_at=0))
        await session.commit()
    expired = await async_client.post("/api/oauth/manual-callback", json={"callbackUrl": f"browser-code#{first_state}"})
    assert expired.json()["status"] == "error"
    assert "expired" in expired.json()["errorMessage"]
    assert oauth_vendor.browser_calls == 0
    assert (await _status(async_client, second["flowId"]))["status"] == "pending"
