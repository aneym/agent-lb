from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config.settings import get_settings

pytestmark = pytest.mark.integration


def _enable_trusted_proxy(
    monkeypatch: pytest.MonkeyPatch,
    *,
    trusted_proxy_cidrs: str = "127.0.0.1/32",
    unauthenticated_cidrs: str = "127.0.0.1/32",
) -> None:
    monkeypatch.setenv("AGENT_LB_FIREWALL_TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("AGENT_LB_FIREWALL_TRUSTED_PROXY_CIDRS", trusted_proxy_cidrs)
    monkeypatch.setenv("AGENT_LB_PROXY_UNAUTHENTICATED_CLIENT_CIDRS", unauthenticated_cidrs)
    get_settings.cache_clear()


def _enable_untrusted_proxy(
    monkeypatch: pytest.MonkeyPatch,
    *,
    unauthenticated_cidrs: str = "127.0.0.1/32",
) -> None:
    monkeypatch.setenv("AGENT_LB_FIREWALL_TRUST_PROXY_HEADERS", "false")
    monkeypatch.setenv("AGENT_LB_PROXY_UNAUTHENTICATED_CLIENT_CIDRS", unauthenticated_cidrs)
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_forwarded_public_request_without_key_is_rejected_even_with_loopback_in_cidr(
    app_instance,
    monkeypatch,
):

    _enable_trusted_proxy(
        monkeypatch,
        trusted_proxy_cidrs="127.0.0.1/32",
        unauthenticated_cidrs="127.0.0.1/32",
    )

    async with app_instance.router.lifespan_context(app_instance):
        transport = ASGITransport(app=app_instance, client=("127.0.0.1", 443))
        async with AsyncClient(transport=transport, base_url="http://lb.example") as client:
            response = await client.get(
                "/v1/models",
                headers={"X-Forwarded-For": "203.0.113.77"},
            )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_direct_loopback_request_without_xff_resolves_to_the_socket_peer(
    app_instance,
    monkeypatch,
):

    _enable_trusted_proxy(
        monkeypatch,
        trusted_proxy_cidrs="127.0.0.1/32",
        unauthenticated_cidrs="127.0.0.1/32",
    )

    async with app_instance.router.lifespan_context(app_instance):
        transport = ASGITransport(app=app_instance, client=("127.0.0.1", 443))
        async with AsyncClient(transport=transport, base_url="http://localhost") as client:
            response = await client.get("/v1/models")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_direct_loopback_request_with_unusable_xff_is_still_blocked(
    app_instance,
    monkeypatch,
):

    _enable_trusted_proxy(
        monkeypatch,
        trusted_proxy_cidrs="127.0.0.1/32",
        unauthenticated_cidrs="127.0.0.1/32",
    )

    async with app_instance.router.lifespan_context(app_instance):
        transport = ASGITransport(app=app_instance, client=("127.0.0.1", 443))
        async with AsyncClient(transport=transport, base_url="http://localhost") as client:
            response = await client.get("/v1/models", headers={"X-Forwarded-For": "not-an-ip"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_xff_spoofing_from_untrusted_peer_is_ignored(app_instance, monkeypatch):

    _enable_trusted_proxy(
        monkeypatch,
        trusted_proxy_cidrs="10.0.0.0/8",
        unauthenticated_cidrs="127.0.0.1/32",
    )

    async with app_instance.router.lifespan_context(app_instance):
        transport = ASGITransport(app=app_instance, client=("198.51.100.99", 54321))
        async with AsyncClient(transport=transport, base_url="http://lb.example") as client:
            response = await client.get(
                "/v1/models",
                headers={"X-Forwarded-For": "127.0.0.1"},
            )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_existing_unauthenticated_cidr_behavior_unchanged_without_proxy_headers(
    app_instance,
    monkeypatch,
):

    _enable_untrusted_proxy(
        monkeypatch,
        unauthenticated_cidrs="192.168.65.1/32",
    )

    async with app_instance.router.lifespan_context(app_instance):
        transport = ASGITransport(app=app_instance, client=("192.168.65.1", 50001))
        async with AsyncClient(transport=transport, base_url="http://lb.example") as client:
            response = await client.get("/v1/models")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_existing_unauthenticated_cidr_excludes_other_peers_unchanged(
    app_instance,
    monkeypatch,
):

    _enable_untrusted_proxy(
        monkeypatch,
        unauthenticated_cidrs="192.168.65.1/32",
    )

    async with app_instance.router.lifespan_context(app_instance):
        transport = ASGITransport(app=app_instance, client=("192.168.65.2", 50001))
        async with AsyncClient(transport=transport, base_url="http://lb.example") as client:
            response = await client.get("/v1/models")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"
