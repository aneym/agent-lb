"""Unit tests for trusted-proxy-aware client IP resolution in proxy auth.

These tests verify that _is_proxy_unauthenticated_client_allowed uses the resolved
(XFF-aware) client IP rather than the raw socket peer IP, so that a loopback entry in
proxy_unauthenticated_client_cidrs cannot act as a bypass for public traffic arriving
through a local reverse proxy such as Tailscale Funnel.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import HTTPConnection

from app.core.auth import dependencies as auth_dependencies
from app.core.exceptions import ProxyAuthError

pytestmark = pytest.mark.unit


def _make_connection(
    *,
    socket_host: str,
    socket_port: int = 12345,
    headers: list[tuple[str, str]] | None = None,
) -> HTTPConnection:
    raw_headers: list[tuple[bytes, bytes]] = [(k.lower().encode(), v.encode()) for k, v in (headers or [])]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/v1/models",
        "headers": raw_headers,
        "client": (socket_host, socket_port),
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "http_version": "1.1",
    }
    return HTTPConnection(scope)


def _set_settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    proxy_unauthenticated_client_cidrs: list[str],
    firewall_trust_proxy_headers: bool = False,
    firewall_trusted_proxy_cidrs: list[str] | None = None,
) -> None:
    if firewall_trusted_proxy_cidrs is None:
        firewall_trusted_proxy_cidrs = ["127.0.0.1/32", "::1/128"]

    fake = SimpleNamespace(
        proxy_unauthenticated_client_cidrs=proxy_unauthenticated_client_cidrs,
        firewall_trust_proxy_headers=firewall_trust_proxy_headers,
        firewall_trusted_proxy_cidrs=firewall_trusted_proxy_cidrs,
    )
    monkeypatch.setattr(auth_dependencies, "get_settings", lambda: fake)
    # Also patch request_locality's get_settings so resolve_request_client_host sees the same config
    import app.core.request_locality as locality_mod

    monkeypatch.setattr(locality_mod, "get_settings", lambda: fake)


# ---------------------------------------------------------------------------
# _is_proxy_unauthenticated_client_allowed
# ---------------------------------------------------------------------------


def test_loopback_socket_peer_allowed_when_in_cidr_and_no_proxy_headers(monkeypatch):
    """Direct loopback connection (no Funnel proxy) is allowed when 127.0.0.1/32 is in CIDRs."""
    _set_settings(monkeypatch, proxy_unauthenticated_client_cidrs=["127.0.0.1/32"])
    conn = _make_connection(socket_host="127.0.0.1")

    assert auth_dependencies._is_proxy_unauthenticated_client_allowed(conn) is True


def test_public_socket_peer_not_allowed_when_not_in_cidr(monkeypatch):
    """A public IP not in the allowlist is rejected."""
    _set_settings(monkeypatch, proxy_unauthenticated_client_cidrs=["192.168.65.0/24"])
    conn = _make_connection(socket_host="203.0.113.5")

    assert auth_dependencies._is_proxy_unauthenticated_client_allowed(conn) is False


def test_funnel_xff_spoofing_from_loopback_is_rejected_when_proxy_headers_untrusted(monkeypatch):
    """XFF spoofing from an untrusted socket peer must be ignored.

    Scenario: socket peer is loopback (127.0.0.1 is in proxy_unauthenticated_client_cidrs),
    but firewall_trust_proxy_headers is False — XFF must NOT be honored.
    The resolved IP stays 127.0.0.1, so the loopback entry in the CIDR allowlist still
    lets the connection through (this is the legitimate local-caller case).
    Spoofed XFF has no effect.
    """
    _set_settings(
        monkeypatch,
        proxy_unauthenticated_client_cidrs=["127.0.0.1/32"],
        firewall_trust_proxy_headers=False,
    )
    conn = _make_connection(
        socket_host="127.0.0.1",
        headers=[("x-forwarded-for", "203.0.113.99")],
    )
    # proxy headers not trusted → resolved IP is still 127.0.0.1 → in CIDRs → allowed
    assert auth_dependencies._is_proxy_unauthenticated_client_allowed(conn) is True


def test_funnel_traffic_with_loopback_in_cidr_is_rejected_when_proxy_headers_trusted(monkeypatch):
    """Core security guarantee: loopback in CIDRs does not allow Funnel-proxied public traffic.

    Scenario: operator sets proxy_unauthenticated_client_cidrs=["127.0.0.1/32"] hoping to
    allow Funnel traffic.  With firewall_trust_proxy_headers=True and 127.0.0.1 in
    firewall_trusted_proxy_cidrs, the resolved IP becomes the real public IP from XFF.
    That public IP is NOT in the unauthenticated CIDR allowlist → rejected.
    """
    _set_settings(
        monkeypatch,
        proxy_unauthenticated_client_cidrs=["127.0.0.1/32"],
        firewall_trust_proxy_headers=True,
        firewall_trusted_proxy_cidrs=["127.0.0.1/32"],
    )
    conn = _make_connection(
        socket_host="127.0.0.1",
        headers=[("x-forwarded-for", "203.0.113.99")],
    )
    # resolved IP = 203.0.113.99 (public), NOT in 127.0.0.1/32 → rejected
    assert auth_dependencies._is_proxy_unauthenticated_client_allowed(conn) is False


def test_funnel_traffic_allowed_when_cidr_explicitly_covers_public_range(monkeypatch):
    """Operator can explicitly allow a known public CIDR (e.g. tailnet egress range)."""
    _set_settings(
        monkeypatch,
        proxy_unauthenticated_client_cidrs=["203.0.113.0/24"],
        firewall_trust_proxy_headers=True,
        firewall_trusted_proxy_cidrs=["127.0.0.1/32"],
    )
    conn = _make_connection(
        socket_host="127.0.0.1",
        headers=[("x-forwarded-for", "203.0.113.50")],
    )
    # resolved IP = 203.0.113.50, which is in 203.0.113.0/24 → allowed
    assert auth_dependencies._is_proxy_unauthenticated_client_allowed(conn) is True


def test_xff_spoofing_from_untrusted_socket_peer_ignored(monkeypatch):
    """XFF from an untrusted socket peer must not be honored.

    Scenario: public IP sends a request directly (not through a trusted proxy) with a
    spoofed XFF pointing to 127.0.0.1.  The resolved IP must remain the actual socket IP.
    """
    _set_settings(
        monkeypatch,
        proxy_unauthenticated_client_cidrs=["127.0.0.1/32"],
        firewall_trust_proxy_headers=True,
        firewall_trusted_proxy_cidrs=["10.0.0.0/8"],  # only 10.x is trusted
    )
    conn = _make_connection(
        socket_host="198.51.100.42",  # untrusted socket peer
        headers=[("x-forwarded-for", "127.0.0.1")],  # spoofed XFF
    )
    # socket peer 198.51.100.42 not in trusted CIDRs → XFF ignored → resolved = 198.51.100.42
    # 198.51.100.42 not in proxy_unauthenticated_client_cidrs → rejected
    assert auth_dependencies._is_proxy_unauthenticated_client_allowed(conn) is False


def test_missing_client_returns_false(monkeypatch):
    """When no client information is present, access is denied."""
    _set_settings(
        monkeypatch,
        proxy_unauthenticated_client_cidrs=["127.0.0.1/32"],
    )
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/v1/models",
        "headers": [],
        "client": None,
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "http_version": "1.1",
    }
    conn = HTTPConnection(scope)
    assert auth_dependencies._is_proxy_unauthenticated_client_allowed(conn) is False


# ---------------------------------------------------------------------------
# validate_proxy_api_key_authorization — end-to-end auth flow with XFF
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_proxy_api_key_auth_enabled_requires_key_for_any_client(monkeypatch):
    """When api_key_auth_enabled is True, any client without a key is rejected."""
    settings_cache_mock = AsyncMock()
    settings_cache_mock.get.return_value = SimpleNamespace(
        api_key_auth_enabled=True,
    )
    monkeypatch.setattr(auth_dependencies, "get_settings_cache", lambda: settings_cache_mock)

    conn = _make_connection(socket_host="127.0.0.1")

    with pytest.raises(ProxyAuthError, match="Missing API key"):
        await auth_dependencies.validate_proxy_api_key_authorization(None, request=conn)


@pytest.mark.asyncio
async def test_validate_proxy_api_key_auth_disabled_local_request_passes(monkeypatch):
    """When api_key_auth_enabled is False, a local (loopback) request passes without a key."""
    settings_cache_mock = AsyncMock()
    settings_cache_mock.get.return_value = SimpleNamespace(
        api_key_auth_enabled=False,
    )
    monkeypatch.setattr(auth_dependencies, "get_settings_cache", lambda: settings_cache_mock)

    # Patch is_local_request to return True (direct loopback, no XFF hint)
    monkeypatch.setattr(auth_dependencies, "is_local_request", lambda _: True)
    monkeypatch.setattr(auth_dependencies, "_is_proxy_unauthenticated_client_allowed", lambda _: False)

    conn = _make_connection(socket_host="127.0.0.1")
    result = await auth_dependencies.validate_proxy_api_key_authorization(None, request=conn)
    assert result is None


@pytest.mark.asyncio
async def test_validate_proxy_api_key_auth_disabled_remote_without_cidr_rejected(monkeypatch):
    """When auth is disabled and client is not local, a request not in the CIDR allowlist is rejected."""
    settings_cache_mock = AsyncMock()
    settings_cache_mock.get.return_value = SimpleNamespace(
        api_key_auth_enabled=False,
    )
    monkeypatch.setattr(auth_dependencies, "get_settings_cache", lambda: settings_cache_mock)
    monkeypatch.setattr(auth_dependencies, "is_local_request", lambda _: False)
    monkeypatch.setattr(auth_dependencies, "_is_proxy_unauthenticated_client_allowed", lambda _: False)

    conn = _make_connection(socket_host="203.0.113.5")
    with pytest.raises(ProxyAuthError, match="Proxy authentication must be configured"):
        await auth_dependencies.validate_proxy_api_key_authorization(None, request=conn)
