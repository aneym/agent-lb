"""Integration tests for trusted-proxy-aware proxy auth hardening.

Validates that the resolved client IP (honoring XFF only from trusted proxies) is used
when checking the unauthenticated CIDR allowlist.  The key scenarios:

1. A forwarded public request without an API key is rejected even when the socket peer
   (127.0.0.1) is in proxy_unauthenticated_client_cidrs.
2. Direct loopback and tailnet behavior remain unchanged.
3. XFF spoofing from an untrusted socket peer is ignored.
"""

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
    """Core security guarantee: a public request forwarded through Tailscale Funnel (socket
    peer = 127.0.0.1, XFF = public IP) must be rejected when api_key_auth is not configured,
    even if 127.0.0.1/32 is in proxy_unauthenticated_client_cidrs.

    With firewall_trust_proxy_headers=True and 127.0.0.1 in trusted proxies the resolved IP
    becomes the real public IP which is NOT in the unauthenticated CIDR allowlist.
    """
    _enable_trusted_proxy(
        monkeypatch,
        trusted_proxy_cidrs="127.0.0.1/32",
        unauthenticated_cidrs="127.0.0.1/32",  # loopback only — intentional "misconfiguration"
    )

    async with app_instance.router.lifespan_context(app_instance):
        # Funnel: socket peer is loopback, real client IP in XFF
        transport = ASGITransport(app=app_instance, client=("127.0.0.1", 443))
        async with AsyncClient(transport=transport, base_url="http://lb.example") as client:
            response = await client.get(
                "/v1/models",
                headers={"X-Forwarded-For": "203.0.113.77"},
            )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_direct_loopback_request_without_xff_blocked_when_proxy_headers_trusted(
    app_instance,
    monkeypatch,
):
    """When firewall_trust_proxy_headers=True and loopback is a trusted proxy source,
    a direct loopback request without any XFF header yields an unresolvable client IP
    (the system cannot distinguish a legitimate local caller from Funnel traffic that
    dropped its XFF).  Such requests are conservatively rejected.

    Local callers in this config should set trust_proxy_headers=False (default) or
    send XFF pointing to their actual IP.
    """
    _enable_trusted_proxy(
        monkeypatch,
        trusted_proxy_cidrs="127.0.0.1/32",
        unauthenticated_cidrs="127.0.0.1/32",
    )

    async with app_instance.router.lifespan_context(app_instance):
        transport = ASGITransport(app=app_instance, client=("127.0.0.1", 443))
        async with AsyncClient(transport=transport, base_url="http://localhost") as client:
            response = await client.get("/v1/models")
    # trusted proxy with no XFF → resolved IP = None → neither local nor in CIDRs → rejected
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_xff_spoofing_from_untrusted_peer_is_ignored(app_instance, monkeypatch):
    """XFF injected by an untrusted socket peer must not grant access.

    Scenario: public IP 198.51.100.99 sends a request with XFF: 127.0.0.1.
    Since 198.51.100.99 is not in firewall_trusted_proxy_cidrs, XFF is ignored and the
    resolved IP remains 198.51.100.99, which is not in proxy_unauthenticated_client_cidrs.
    """
    _enable_trusted_proxy(
        monkeypatch,
        trusted_proxy_cidrs="10.0.0.0/8",  # trusted = private 10.x only
        unauthenticated_cidrs="127.0.0.1/32",
    )

    async with app_instance.router.lifespan_context(app_instance):
        transport = ASGITransport(app=app_instance, client=("198.51.100.99", 54321))
        async with AsyncClient(transport=transport, base_url="http://lb.example") as client:
            response = await client.get(
                "/v1/models",
                headers={"X-Forwarded-For": "127.0.0.1"},  # spoofed
            )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_existing_unauthenticated_cidr_behavior_unchanged_without_proxy_headers(
    app_instance,
    monkeypatch,
):
    """Backward compatibility: when trust_proxy_headers=False, socket peer IP is used directly.

    An explicit non-loopback CIDR allowlist entry must still work for direct connections
    (the pre-existing proxy_unauthenticated_client_cidrs feature).
    """
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
    """Backward compatibility: peers outside the allowlist CIDR are still rejected."""
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
