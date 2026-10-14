

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

    import app.core.request_locality as locality_mod

    monkeypatch.setattr(locality_mod, "get_settings", lambda: fake)







def test_loopback_socket_peer_allowed_when_in_cidr_and_no_proxy_headers(monkeypatch):

    _set_settings(monkeypatch, proxy_unauthenticated_client_cidrs=["127.0.0.1/32"])
    conn = _make_connection(socket_host="127.0.0.1")

    assert auth_dependencies._is_proxy_unauthenticated_client_allowed(conn) is True


def test_public_socket_peer_not_allowed_when_not_in_cidr(monkeypatch):

    _set_settings(monkeypatch, proxy_unauthenticated_client_cidrs=["192.168.65.0/24"])
    conn = _make_connection(socket_host="203.0.113.5")

    assert auth_dependencies._is_proxy_unauthenticated_client_allowed(conn) is False


def test_funnel_xff_spoofing_from_loopback_is_rejected_when_proxy_headers_untrusted(monkeypatch):

    _set_settings(
        monkeypatch,
        proxy_unauthenticated_client_cidrs=["127.0.0.1/32"],
        firewall_trust_proxy_headers=False,
    )
    conn = _make_connection(
        socket_host="127.0.0.1",
        headers=[("x-forwarded-for", "203.0.113.99")],
    )

    assert auth_dependencies._is_proxy_unauthenticated_client_allowed(conn) is True


def test_funnel_traffic_with_loopback_in_cidr_is_rejected_when_proxy_headers_trusted(monkeypatch):

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

    assert auth_dependencies._is_proxy_unauthenticated_client_allowed(conn) is False


def test_funnel_traffic_allowed_when_cidr_explicitly_covers_public_range(monkeypatch):

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

    assert auth_dependencies._is_proxy_unauthenticated_client_allowed(conn) is True


def test_xff_spoofing_from_untrusted_socket_peer_ignored(monkeypatch):

    _set_settings(
        monkeypatch,
        proxy_unauthenticated_client_cidrs=["127.0.0.1/32"],
        firewall_trust_proxy_headers=True,
        firewall_trusted_proxy_cidrs=["10.0.0.0/8"],
    )
    conn = _make_connection(
        socket_host="198.51.100.42",
        headers=[("x-forwarded-for", "127.0.0.1")],
    )


    assert auth_dependencies._is_proxy_unauthenticated_client_allowed(conn) is False


def test_missing_client_returns_false(monkeypatch):

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







@pytest.mark.asyncio
async def test_validate_proxy_api_key_auth_enabled_requires_key_for_any_client(monkeypatch):

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

    settings_cache_mock = AsyncMock()
    settings_cache_mock.get.return_value = SimpleNamespace(
        api_key_auth_enabled=False,
    )
    monkeypatch.setattr(auth_dependencies, "get_settings_cache", lambda: settings_cache_mock)


    monkeypatch.setattr(auth_dependencies, "is_local_request", lambda _: True)
    monkeypatch.setattr(auth_dependencies, "_is_proxy_unauthenticated_client_allowed", lambda _: False)

    conn = _make_connection(socket_host="127.0.0.1")
    result = await auth_dependencies.validate_proxy_api_key_authorization(None, request=conn)
    assert result is None


@pytest.mark.asyncio
async def test_validate_proxy_api_key_auth_disabled_remote_without_cidr_rejected(monkeypatch):

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
