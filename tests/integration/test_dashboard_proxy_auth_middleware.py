from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.config.settings import get_settings
from app.core.middleware.dashboard_auth_proxy import add_dashboard_auth_proxy_middleware

pytestmark = pytest.mark.integration


def _set_dashboard_auth_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: DashboardAuthMode,
    trust_proxy_headers: bool = False,
    trusted_proxy_cidrs: str = "127.0.0.1/32",
    proxy_header: str = "Remote-User",
) -> None:
    monkeypatch.setenv("AGENT_LB_DASHBOARD_AUTH_MODE", mode)
    monkeypatch.setenv("AGENT_LB_FIREWALL_TRUST_PROXY_HEADERS", str(trust_proxy_headers).lower())
    monkeypatch.setenv("AGENT_LB_FIREWALL_TRUSTED_PROXY_CIDRS", trusted_proxy_cidrs)
    monkeypatch.setenv("AGENT_LB_DASHBOARD_AUTH_PROXY_HEADER", proxy_header)
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_trusted_header_mode_requires_proxy_header_for_open_dashboard(async_client, monkeypatch):
    _set_dashboard_auth_env(
        monkeypatch,
        mode=DashboardAuthMode.TRUSTED_HEADER,
        trust_proxy_headers=True,
    )

    session = await async_client.get("/api/dashboard-auth/session")
    assert session.status_code == 200
    assert session.json() == {
        "authenticated": False,
        "passwordRequired": False,
        "totpRequiredOnLogin": False,
        "totpConfigured": False,
        "bootstrapRequired": False,
        "bootstrapTokenConfigured": False,
        "authMode": "trusted_header",
        "passwordManagementEnabled": True,
        "passwordSessionActive": False,
    }

    blocked = await async_client.get("/api/settings")
    assert blocked.status_code == 401
    assert blocked.json()["error"]["code"] == "proxy_auth_required"


@pytest.mark.asyncio
async def test_trusted_header_mode_allows_proxy_header_and_password_fallback(async_client, monkeypatch):
    _set_dashboard_auth_env(
        monkeypatch,
        mode=DashboardAuthMode.TRUSTED_HEADER,
        trust_proxy_headers=True,
    )

    setup_without_proxy = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup_without_proxy.status_code == 401
    assert setup_without_proxy.json()["error"]["code"] == "proxy_auth_required"

    proxy_headers = {"Remote-User": "admin@example.com"}
    proxy_session = await async_client.get("/api/dashboard-auth/session", headers=proxy_headers)
    assert proxy_session.status_code == 200
    assert proxy_session.json()["authenticated"] is True
    assert proxy_session.json()["authMode"] == "trusted_header"

    allowed = await async_client.get("/api/settings", headers=proxy_headers)
    assert allowed.status_code == 200

    setup_with_proxy = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
        headers=proxy_headers,
    )
    assert setup_with_proxy.status_code == 200
    assert setup_with_proxy.json()["authMode"] == "trusted_header"

    async_client.cookies.clear()
    blocked = await async_client.get("/api/settings")
    assert blocked.status_code == 401
    assert blocked.json()["error"]["code"] == "authentication_required"

    fallback_login = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"password": "password123"},
    )
    assert fallback_login.status_code == 200
    assert fallback_login.json()["authMode"] == "trusted_header"

    allowed_with_password = await async_client.get("/api/settings")
    assert allowed_with_password.status_code == 200


@pytest.mark.asyncio
async def test_disabled_dashboard_auth_mode_bypasses_guard_and_disables_password_flows(async_client, monkeypatch):
    _set_dashboard_auth_env(monkeypatch, mode=DashboardAuthMode.DISABLED)

    session = await async_client.get("/api/dashboard-auth/session")
    assert session.status_code == 200
    assert session.json() == {
        "authenticated": True,
        "passwordRequired": False,
        "totpRequiredOnLogin": False,
        "totpConfigured": False,
        "bootstrapRequired": False,
        "bootstrapTokenConfigured": False,
        "authMode": "disabled",
        "passwordManagementEnabled": False,
        "passwordSessionActive": False,
    }

    allowed = await async_client.get("/api/settings")
    assert allowed.status_code == 200

    setup = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
    )
    assert setup.status_code == 400
    assert setup.json()["error"]["code"] == "password_management_disabled"

    login = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"password": "password123"},
    )
    assert login.status_code == 400
    assert login.json()["error"]["code"] == "password_management_disabled"

    start_totp = await async_client.post("/api/dashboard-auth/totp/setup/start", json={})
    assert start_totp.status_code == 400
    assert start_totp.json()["error"]["code"] == "password_management_disabled"

    disable_totp = await async_client.post("/api/dashboard-auth/totp/disable", json={"code": "123456"})
    assert disable_totp.status_code == 400
    assert disable_totp.json()["error"]["code"] == "password_management_disabled"


@pytest.mark.asyncio
async def test_trusted_header_proxy_auth_with_fallback_password_reports_no_active_session(async_client, monkeypatch):

    _set_dashboard_auth_env(
        monkeypatch,
        mode=DashboardAuthMode.TRUSTED_HEADER,
        trust_proxy_headers=True,
    )
    proxy_headers = {"Remote-User": "admin@example.com"}

    setup = await async_client.post(
        "/api/dashboard-auth/password/setup",
        json={"password": "password123"},
        headers=proxy_headers,
    )
    assert setup.status_code == 200
    assert setup.json()["passwordSessionActive"] is True

    async_client.cookies.clear()

    session = await async_client.get("/api/dashboard-auth/session", headers=proxy_headers)
    assert session.status_code == 200
    body = session.json()
    assert body["authenticated"] is True
    assert body["authMode"] == "trusted_header"
    assert body["passwordRequired"] is True
    assert body["passwordManagementEnabled"] is True
    assert body["passwordSessionActive"] is False

    fallback_login = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"password": "password123"},
    )
    assert fallback_login.status_code == 200
    assert fallback_login.json()["passwordSessionActive"] is True

    session_after_login = await async_client.get("/api/dashboard-auth/session", headers=proxy_headers)
    assert session_after_login.json()["passwordSessionActive"] is True


@pytest.mark.asyncio
async def test_trusted_header_mode_scrubs_untrusted_proxy_header(monkeypatch):
    _set_dashboard_auth_env(
        monkeypatch,
        mode=DashboardAuthMode.TRUSTED_HEADER,
        trust_proxy_headers=True,
        trusted_proxy_cidrs="10.0.0.0/8",
    )
    app = FastAPI()
    add_dashboard_auth_proxy_middleware(app)

    @app.get("/dashboard-proxy-header")
    async def echo_dashboard_proxy_header(request: Request) -> dict[str, str | None]:
        return {"remote_user": request.headers.get("Remote-User")}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/dashboard-proxy-header",
            headers={"Remote-User": "attacker@example.com"},
        )

    assert response.status_code == 200
    assert response.json() == {"remote_user": None}
