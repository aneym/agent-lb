from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config.settings import get_settings

pytestmark = pytest.mark.integration

STUDIO_TAILNET_IP = "100.107.143.41"


@pytest_asyncio.fixture
async def studio_app(_reset_db_state, monkeypatch):
    del _reset_db_state
    monkeypatch.setenv("AGENT_LB_DASHBOARD_AUTH_MODE", "disabled")
    monkeypatch.setenv("AGENT_LB_FIREWALL_TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("AGENT_LB_FIREWALL_TRUSTED_PROXY_CIDRS", "127.0.0.1/32,::1/128")
    monkeypatch.setenv(
        "AGENT_LB_PROXY_UNAUTHENTICATED_CLIENT_CIDRS",
        f"127.0.0.1/32,::1/128,{STUDIO_TAILNET_IP}/32",
    )
    get_settings.cache_clear()
    import app.main as main_module

    async def _noop_init_db() -> None:
        return None

    monkeypatch.setattr(main_module, "init_db", _noop_init_db)
    app = main_module.create_app()
    async with app.router.lifespan_context(app):
        yield app
    get_settings.cache_clear()


async def _get(app, base_url: str, path: str, headers: dict[str, str] | None = None):
    transport = ASGITransport(app=app, client=("127.0.0.1", 50123))
    async with AsyncClient(transport=transport, base_url=base_url) as client:
        return await client.get(path, headers=headers or {})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url,headers,expected",
    [
        ("http://localhost:2455", {}, 200),
        ("http://127.0.0.1:2455", {}, 200),
        ("http://[::1]:2455", {}, 200),
        ("http://host.docker.internal:2455", {}, 403),
        ("http://0.250.250.254", {}, 403),
        ("http://gateway.docker.internal:2455", {}, 403),
        ("http://docker.for.mac.localhost:2455", {}, 403),
        ("http://agent-lb.orb.internal:2455", {}, 403),
        ("http://host.orb.local:2455", {}, 403),
        ("http://host.docker.internal:2455", {"x-forwarded-for": "127.0.0.1"}, 403),
        ("http://host.docker.internal:2455", {"x-forwarded-for": STUDIO_TAILNET_IP}, 403),
        (
            "https://studio.tailf266ac.ts.net:2455",
            {"x-forwarded-for": STUDIO_TAILNET_IP, "x-forwarded-proto": "https"},
            200,
        ),
        (
            "https://studio.tailf266ac.ts.net:2455",
            {"x-forwarded-for": "100.80.22.52", "x-forwarded-proto": "https"},
            200,
        ),
    ],
)
async def test_unkeyed_admin_route_host_trust(studio_app, base_url, headers, expected):
    response = await _get(studio_app, base_url, "/api/request-logs?limit=1", headers)
    assert response.status_code == expected, response.text
    if expected == 403:
        assert response.json()["error"]["code"] == "untrusted_local_host"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/api/settings", "/internal/drain/status"])
async def test_docker_host_blocked_on_dashboard_routes(studio_app, path):
    response = await _get(studio_app, "http://host.docker.internal:2455", path)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "untrusted_local_host"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/models", "/v1/usage", "/backend-api/codex/models", "/health"])
async def test_model_proxy_and_health_unaffected_by_docker_host(studio_app, path):
    local = await _get(studio_app, "http://localhost:2455", path)
    docker = await _get(studio_app, "http://host.docker.internal:2455", path)
    assert docker.status_code == local.status_code, docker.text
    assert "untrusted_local_host" not in docker.text
