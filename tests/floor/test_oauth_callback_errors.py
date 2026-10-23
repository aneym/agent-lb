from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.responses import JSONResponse

import app.modules.oauth.service as oauth_module
from app.core.clients.oauth import OAuthError
from app.core.crypto import TokenEncryptor
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.oauth import api as oauth_api_module
from app.modules.oauth.repository import OAuthFlowRecord, OAuthFlowRepository
from app.modules.oauth.schemas import ManualCallbackRequest
from tests.floor.oauth_flow_support import (
    _oauth_state_token,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_manual_callback_api_sanitizes_unexpected_exception():
    class FailingOauthService:
        async def manual_callback(self, callback_url: str, flow_id: str | None = None):
            raise RuntimeError("Traceback (most recent call last): password=super-secret")

    response = cast(
        JSONResponse,
        await oauth_api_module.manual_callback(
            ManualCallbackRequest(callback_url="http://localhost:1455/?code=c&state=s"),
            context=cast(Any, SimpleNamespace(service=FailingOauthService())),
        ),
    )

    assert response.status_code == 500
    payload = json.loads(bytes(response.body))
    assert payload == {
        "error": {
            "code": "manual_callback_failed",
            "message": "An internal error occurred.",
        }
    }
    assert "super-secret" not in bytes(response.body).decode()


@pytest.mark.asyncio
async def test_manual_callback_api_preserves_oauth_error():
    class FailingOauthService:
        async def manual_callback(self, callback_url: str, flow_id: str | None = None):
            raise OAuthError("invalid_grant", "Authorization code expired", status_code=400)

    response = cast(
        JSONResponse,
        await oauth_api_module.manual_callback(
            ManualCallbackRequest(callback_url="http://localhost:1455/?code=c&state=s"),
            context=cast(Any, SimpleNamespace(service=FailingOauthService())),
        ),
    )

    assert response.status_code == 502
    assert json.loads(bytes(response.body)) == {
        "error": {
            "code": "invalid_grant",
            "message": "Authorization code expired",
        }
    }


@pytest.mark.asyncio
async def test_manual_callback_service_sanitizes_unexpected_exception(db_setup, monkeypatch, caplog):
    await oauth_module._OAUTH_STORE.reset()
    caplog.set_level(logging.ERROR, logger=oauth_module.logger.name)
    async with oauth_module._OAUTH_STORE.lock:
        oauth_module._OAUTH_STORE.remember_flow_locked(
            oauth_module.OAuthState(
                flow_id="flow-1",
                status="pending",
                method="browser",
                state_token="state-1",
                code_verifier="verifier-1",
            )
        )

    async def fake_oauth_route():
        return None

    async def fake_exchange_authorization_code(**_kwargs):
        raise RuntimeError("Unexpected error: /home/app/password.txt")

    monkeypatch.setattr(oauth_module, "_oauth_route", fake_oauth_route)
    monkeypatch.setattr(oauth_module, "exchange_authorization_code", fake_exchange_authorization_code)
    service = oauth_module.OauthService(cast(AccountsRepository, SimpleNamespace()))

    async with SessionLocal() as session:
        await OAuthFlowRepository(session, TokenEncryptor()).create(
            OAuthFlowRecord(
                flow_id="flow-1", method="browser", status="pending", state_token="state-1", code_verifier="verifier-1"
            )
        )

    response = await service.manual_callback("http://localhost:1455/?code=code-1&state=state-1", flow_id="flow-1")

    assert response.status == "error"
    assert response.error_message == "An internal error occurred."
    assert "RuntimeError" in caplog.text
    assert "password.txt" not in caplog.text
    assert "/home/app" not in caplog.text
    assert "Traceback" not in caplog.text
    async with oauth_module._OAUTH_STORE.lock:
        flow = oauth_module._OAUTH_STORE.get_flow_locked("flow-1")
        assert flow is not None
        assert flow.error_message == "An internal error occurred."


def test_oauth_error_html_escapes_message():
    html = oauth_module._error_html("bad <script>alert('x')</script>")

    assert "<script>" not in html
    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in html


@pytest.mark.asyncio
async def test_manual_callback_returns_error_message_for_invalid_state(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    async def fake_callback_server_start(self) -> None:
        return None

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)

    start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})
    assert start.status_code == 200
    payload = start.json()
    assert payload["method"] == "browser"

    response = await async_client.post(
        "/api/oauth/manual-callback",
        json={
            "callbackUrl": "http://localhost:1455/auth/callback?code=manual-code&state=wrong",
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "error",
        "errorMessage": "Invalid OAuth callback: state mismatch or missing code.",
    }

    status = await async_client.get("/api/oauth/status")
    assert status.status_code == 200
    assert status.json() == {"status": "pending", "errorMessage": None}

    flow_status = await async_client.get("/api/oauth/status", params={"flowId": payload["flowId"]})
    assert flow_status.status_code == 200
    assert flow_status.json() == {"status": "pending", "errorMessage": None}


@pytest.mark.asyncio
async def test_manual_callback_error_resolves_state_before_marking_flow_failed(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    async def fake_callback_server_start(self) -> None:
        return None

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)

    first_start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})
    assert first_start.status_code == 200
    first_payload = first_start.json()
    first_error_url = (
        "http://localhost:1455/auth/callback?error=access_denied&state="
        f"{_oauth_state_token(first_payload['authorizationUrl'])}"
    )

    second_start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})
    assert second_start.status_code == 200
    second_payload = second_start.json()
    assert second_payload["flowId"] != first_payload["flowId"]

    mismatched_response = await async_client.post(
        "/api/oauth/manual-callback",
        json={
            "callbackUrl": first_error_url,
            "flowId": second_payload["flowId"],
        },
    )
    assert mismatched_response.status_code == 200
    assert mismatched_response.json() == {
        "status": "error",
        "errorMessage": "OAuth error: access_denied",
    }

    first_status = await async_client.get("/api/oauth/status", params={"flowId": first_payload["flowId"]})
    assert first_status.status_code == 200
    assert first_status.json() == {"status": "pending", "errorMessage": None}

    second_status = await async_client.get("/api/oauth/status", params={"flowId": second_payload["flowId"]})
    assert second_status.status_code == 200
    assert second_status.json() == {"status": "pending", "errorMessage": None}

    matching_response = await async_client.post(
        "/api/oauth/manual-callback",
        json={
            "callbackUrl": first_error_url,
            "flowId": first_payload["flowId"],
        },
    )
    assert matching_response.status_code == 200
    assert matching_response.json() == {
        "status": "error",
        "errorMessage": "OAuth error: access_denied",
    }

    first_status = await async_client.get("/api/oauth/status", params={"flowId": first_payload["flowId"]})
    assert first_status.status_code == 200
    assert first_status.json() == {
        "status": "error",
        "errorMessage": "OAuth error: access_denied",
    }

    second_status = await async_client.get("/api/oauth/status", params={"flowId": second_payload["flowId"]})
    assert second_status.status_code == 200
    assert second_status.json() == {"status": "pending", "errorMessage": None}


@pytest.mark.asyncio
async def test_unknown_flow_error_does_not_mutate_latest_oauth_status():
    await oauth_module._OAUTH_STORE.reset()
    async with SessionLocal() as session:
        service = oauth_module.OauthService(AccountsRepository(session))

        async with oauth_module._OAUTH_STORE.lock:
            latest = oauth_module.OAuthState(
                flow_id="latest-flow",
                status="pending",
                method="browser",
                state_token="latest-state",
                code_verifier="latest-verifier",
            )
            oauth_module._OAUTH_STORE.remember_flow_locked(latest)
            oauth_module._OAUTH_STORE.set_latest_flow_locked(latest)

        await service._set_error("wrong flow", flow_id="missing-flow")

        async with oauth_module._OAUTH_STORE.lock:
            latest_state = oauth_module._OAUTH_STORE.state
            latest_flow = oauth_module._OAUTH_STORE.get_flow_locked("latest-flow")

    assert latest_state.status == "pending"
    assert latest_state.error_message is None
    assert latest_flow is not None
    assert latest_flow.status == "pending"
    assert latest_flow.error_message is None


@pytest.mark.asyncio
async def test_missing_flow_error_does_not_mutate_latest_oauth_status():
    await oauth_module._OAUTH_STORE.reset()
    async with SessionLocal() as session:
        service = oauth_module.OauthService(AccountsRepository(session))

        async with oauth_module._OAUTH_STORE.lock:
            latest = oauth_module.OAuthState(
                flow_id="latest-flow",
                status="pending",
                method="browser",
                state_token="latest-state",
                code_verifier="latest-verifier",
            )
            oauth_module._OAUTH_STORE.remember_flow_locked(latest)
            oauth_module._OAUTH_STORE.set_latest_flow_locked(latest)

        await service._set_error("wrong flow")

        async with oauth_module._OAUTH_STORE.lock:
            latest_state = oauth_module._OAUTH_STORE.state
            latest_flow = oauth_module._OAUTH_STORE.get_flow_locked("latest-flow")

    assert latest_state.status == "pending"
    assert latest_state.error_message is None
    assert latest_flow is not None
    assert latest_flow.status == "pending"
    assert latest_flow.error_message is None


@pytest.mark.asyncio
async def test_manual_callback_unknown_state_does_not_mutate_latest_flow(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    async def fake_callback_server_start(self) -> None:
        return None

    monkeypatch.setattr(oauth_module.OAuthCallbackServer, "start", fake_callback_server_start)

    start = await async_client.post("/api/oauth/start", json={"forceMethod": "browser"})
    assert start.status_code == 200
    payload = start.json()

    response = await async_client.post(
        "/api/oauth/manual-callback",
        json={
            "callbackUrl": "http://localhost:1455/auth/callback?error=access_denied&state=missing-state",
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "error",
        "errorMessage": "OAuth error: access_denied",
    }

    status = await async_client.get("/api/oauth/status", params={"flowId": payload["flowId"]})
    assert status.status_code == 200
    assert status.json() == {"status": "pending", "errorMessage": None}
