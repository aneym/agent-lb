from __future__ import annotations

import pytest

import app.modules.oauth.service as oauth_module
from app.core.auth import generate_unique_account_id
from app.core.clients.oauth import OAuthTokens
from app.core.config.settings import Settings
from app.modules.oauth import api as oauth_api_module
from tests.integration.oauth_flow_support import (
    _encode_jwt,
    _oauth_state_token,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_manual_callback_returns_success_and_creates_account(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    async def fake_callback_server_start(self) -> None:
        return None

    email = "manual@example.com"
    raw_account_id = "acc_manual"

    async def fake_exchange_authorization_code(**_):
        payload = {
            "email": email,
            "chatgpt_account_id": raw_account_id,
            "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
        }
        return OAuthTokens(
            access_token="manual-access-token",
            refresh_token="manual-refresh-token",
            id_token=_encode_jwt(payload),
        )

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)
    monkeypatch.setattr(oauth_module, "exchange_authorization_code", fake_exchange_authorization_code)

    start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})
    assert start.status_code == 200
    payload = start.json()
    assert payload["method"] == "browser"

    async with oauth_module._OAUTH_STORE.lock:
        state_token = oauth_module._OAUTH_STORE.state.state_token

    response = await async_client.post(
        "/api/oauth/manual-callback",
        json={
            "callbackUrl": f"http://localhost:1455/auth/callback?code=manual-code&state={state_token}",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"status": "success", "errorMessage": None}

    status = await async_client.get("/api/oauth/status")
    assert status.status_code == 200
    assert status.json()["status"] == "success"

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    accounts = await async_client.get("/api/accounts")
    assert accounts.status_code == 200
    data = accounts.json()["accounts"]
    assert any(account["accountId"] == expected_account_id for account in data)


@pytest.mark.asyncio
async def test_anthropic_manual_callback_persists_provider_account(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    async def fake_callback_server_start(self) -> None:
        return None

    async def fake_exchange_anthropic_authorization_code(**kwargs):
        assert kwargs["code"] == "anthropic-code"
        return OAuthTokens(
            access_token="anthropic-access-token",
            refresh_token="anthropic-refresh-token",
            id_token=None,
            account_id="claude_user_123",
            email="claude@example.com",
            plan_type="max",
        )

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)
    monkeypatch.setattr(
        oauth_module,
        "exchange_anthropic_authorization_code",
        fake_exchange_anthropic_authorization_code,
    )

    start = await async_client.post(
        "/api/oauth/start",
        json={"forceMethod": "browser", "provider": "anthropic"},
    )
    assert start.status_code == 200
    payload = start.json()
    assert payload["method"] == "browser"
    assert payload["callbackUrl"] == "https://platform.claude.com/oauth/code/callback"
    assert "claude.com/cai/oauth/authorize" in payload["authorizationUrl"]
    assert len(_oauth_state_token(payload["authorizationUrl"])) == 43

    response = await async_client.post(
        "/api/oauth/manual-callback",
        json={
            "callbackUrl": (f"anthropic-code#{_oauth_state_token(payload['authorizationUrl'])}"),
            "flowId": payload["flowId"],
        },
    )
    assert response.status_code == 200
    assert response.json() == {"status": "success", "errorMessage": None}

    accounts = await async_client.get("/api/accounts")
    assert accounts.status_code == 200
    data = accounts.json()["accounts"]
    anthropic_accounts = [account for account in data if account["provider"] == "anthropic"]
    assert len(anthropic_accounts) == 1
    assert anthropic_accounts[0]["accountId"] == generate_unique_account_id("claude_user_123", "claude@example.com")
    assert anthropic_accounts[0]["email"] == "claude@example.com"
    assert anthropic_accounts[0]["planType"] == "max"


@pytest.mark.asyncio
async def test_oauth_status_binds_camel_case_flow_id(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    async def fake_callback_server_start(self) -> None:
        return None

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)

    first_start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})
    assert first_start.status_code == 200
    first_payload = first_start.json()
    assert first_payload["flowId"]

    second_start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})
    assert second_start.status_code == 200
    second_payload = second_start.json()
    assert second_payload["flowId"]
    assert second_payload["flowId"] != first_payload["flowId"]

    error_response = await async_client.post(
        "/api/oauth/manual-callback",
        json={
            "callbackUrl": "http://localhost:1455/auth/callback?code=manual-code&state=wrong",
            "flowId": second_payload["flowId"],
        },
    )
    assert error_response.status_code == 200
    assert error_response.json()["status"] == "error"

    first_status = await async_client.get("/api/oauth/status", params={"flowId": first_payload["flowId"]})
    assert first_status.status_code == 200
    assert first_status.json() == {"status": "pending", "errorMessage": None}

    second_status = await async_client.get("/api/oauth/status", params={"flowId": second_payload["flowId"]})
    assert second_status.status_code == 200
    assert second_status.json() == {"status": "pending", "errorMessage": None}

    typo_status = await async_client.get("/api/oauth/status", params={"flowId": f"{second_payload['flowId']}-typo"})
    assert typo_status.status_code == 200
    assert typo_status.json() == {"status": "pending", "errorMessage": None}

    latest_status = await async_client.get("/api/oauth/status")
    assert latest_status.status_code == 200
    assert latest_status.json() == {"status": "pending", "errorMessage": None}


@pytest.mark.asyncio
async def test_concurrent_browser_oauth_flows_keep_callbacks_isolated(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    async def fake_callback_server_start(self) -> None:
        return None

    async def fake_exchange_authorization_code(**kwargs):
        code = kwargs["code"]
        payload = {
            "email": f"{code}@example.com",
            "chatgpt_account_id": f"acc_{code}",
            "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
        }
        return OAuthTokens(
            access_token=f"access-{code}",
            refresh_token=f"refresh-{code}",
            id_token=_encode_jwt(payload),
        )

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)
    monkeypatch.setattr(oauth_module, "exchange_authorization_code", fake_exchange_authorization_code)

    first_start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})
    assert first_start.status_code == 200
    first_payload = first_start.json()
    assert first_payload["method"] == "browser"
    assert first_payload["flowId"]

    second_start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})
    assert second_start.status_code == 200
    second_payload = second_start.json()
    assert second_payload["method"] == "browser"
    assert second_payload["flowId"]
    assert second_payload["flowId"] != first_payload["flowId"]

    first_response = await async_client.post(
        "/api/oauth/manual-callback",
        json={
            "callbackUrl": (
                f"http://localhost:1455/auth/callback?code=code-first&state="
                f"{_oauth_state_token(first_payload['authorizationUrl'])}"
            ),
            "flowId": first_payload["flowId"],
        },
    )
    assert first_response.status_code == 200
    assert first_response.json() == {"status": "success", "errorMessage": None}

    second_response = await async_client.post(
        "/api/oauth/manual-callback",
        json={
            "callbackUrl": (
                f"http://localhost:1455/auth/callback?code=code-second&state="
                f"{_oauth_state_token(second_payload['authorizationUrl'])}"
            ),
            "flowId": second_payload["flowId"],
        },
    )
    assert second_response.status_code == 200
    assert second_response.json() == {"status": "success", "errorMessage": None}

    first_status = await async_client.get("/api/oauth/status", params={"flowId": first_payload["flowId"]})
    assert first_status.status_code == 200
    assert first_status.json() == {"status": "success", "errorMessage": None}

    second_status = await async_client.get("/api/oauth/status", params={"flowId": second_payload["flowId"]})
    assert second_status.status_code == 200
    assert second_status.json() == {"status": "success", "errorMessage": None}

    accounts = await async_client.get("/api/accounts")
    assert accounts.status_code == 200
    data = accounts.json()["accounts"]
    expected_ids = {
        generate_unique_account_id("acc_code-first", "code-first@example.com"),
        generate_unique_account_id("acc_code-second", "code-second@example.com"),
    }
    assert expected_ids.issubset({account["accountId"] for account in data})


@pytest.mark.asyncio
async def test_manual_callback_idempotent_success_requires_requested_flow(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    async def fake_callback_server_start(self) -> None:
        return None

    exchange_calls: list[str] = []

    async def fake_exchange_authorization_code(**kwargs):
        code = kwargs["code"]
        exchange_calls.append(code)
        payload = {
            "email": f"{code}@example.com",
            "chatgpt_account_id": f"acc_{code}",
            "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
        }
        return OAuthTokens(
            access_token=f"access-{code}",
            refresh_token=f"refresh-{code}",
            id_token=_encode_jwt(payload),
        )

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)
    monkeypatch.setattr(oauth_module, "exchange_authorization_code", fake_exchange_authorization_code)

    first_start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})
    assert first_start.status_code == 200
    first_payload = first_start.json()
    first_callback_url = (
        f"http://localhost:1455/auth/callback?code=code-first&state="
        f"{_oauth_state_token(first_payload['authorizationUrl'])}"
    )

    second_start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})
    assert second_start.status_code == 200
    second_payload = second_start.json()
    assert second_payload["flowId"] != first_payload["flowId"]

    first_response = await async_client.post(
        "/api/oauth/manual-callback",
        json={
            "callbackUrl": first_callback_url,
            "flowId": first_payload["flowId"],
        },
    )
    assert first_response.status_code == 200
    assert first_response.json() == {"status": "success", "errorMessage": None}

    replay_with_wrong_flow = await async_client.post(
        "/api/oauth/manual-callback",
        json={
            "callbackUrl": first_callback_url,
            "flowId": second_payload["flowId"],
        },
    )
    assert replay_with_wrong_flow.status_code == 200
    assert replay_with_wrong_flow.json() == {
        "status": "error",
        "errorMessage": "Invalid OAuth callback: state mismatch or missing code.",
    }
    assert exchange_calls == ["code-first"]

    first_status = await async_client.get("/api/oauth/status", params={"flowId": first_payload["flowId"]})
    assert first_status.status_code == 200
    assert first_status.json() == {"status": "success", "errorMessage": None}

    second_status = await async_client.get("/api/oauth/status", params={"flowId": second_payload["flowId"]})
    assert second_status.status_code == 200
    assert second_status.json() == {"status": "pending", "errorMessage": None}


@pytest.mark.asyncio
async def test_oauth_endpoints_blocked_on_federation_follower(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    owner_url = "https://studio.example.ts.net:2455"
    follower_settings = Settings(federation_peer_url=owner_url)
    monkeypatch.setattr(oauth_api_module, "get_settings", lambda: follower_settings)

    start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})
    assert start.status_code == 409
    body = start.json()
    assert body["error"]["code"] == "oauth_owner_required"
    assert owner_url in body["error"]["message"]

    complete = await async_client.post("/api/oauth/complete", json={})
    assert complete.status_code == 409
    assert complete.json()["error"]["code"] == "oauth_owner_required"

    manual = await async_client.post(
        "/api/oauth/manual-callback",
        json={"callbackUrl": "http://localhost:1455/auth/callback?code=c&state=s"},
    )
    assert manual.status_code == 409
    assert manual.json()["error"]["code"] == "oauth_owner_required"

    async with oauth_module._OAUTH_STORE.lock:
        assert oauth_module._OAUTH_STORE._flows == {}
