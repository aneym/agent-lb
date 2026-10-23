from __future__ import annotations

from collections.abc import Mapping
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network

from starlette.datastructures import Headers
from starlette.requests import HTTPConnection

from app.core.config.settings import get_settings
from app.core.forwarded_headers import _HTTP_TOKEN_CHARACTERS as _HTTP_TOKEN_CHARACTERS
from app.core.forwarded_headers import _is_forwarded_quoted_character as _is_forwarded_quoted_character
from app.core.forwarded_headers import _is_http_token as _is_http_token
from app.core.forwarded_headers import _is_trusted_proxy_source as _is_trusted_proxy_source
from app.core.forwarded_headers import _is_valid_ip as _is_valid_ip
from app.core.forwarded_headers import _parse_forwarded_for_chain as _parse_forwarded_for_chain
from app.core.forwarded_headers import _parse_forwarded_node as _parse_forwarded_node
from app.core.forwarded_headers import _parse_forwarded_pair as _parse_forwarded_pair
from app.core.forwarded_headers import (
    _resolve_client_ip_from_forwarded_chain as _resolve_client_ip_from_forwarded_chain,
)
from app.core.forwarded_headers import _resolve_client_ip_from_hops as _resolve_client_ip_from_hops
from app.core.forwarded_headers import _resolve_client_ip_from_xff_chain as _resolve_client_ip_from_xff_chain
from app.core.forwarded_headers import _resolve_forwarded_header_ip as _resolve_forwarded_header_ip
from app.core.forwarded_headers import _split_forwarded_value as _split_forwarded_value
from app.core.forwarded_headers import _unquote_forwarded_value as _unquote_forwarded_value
from app.core.forwarded_headers import _validate_forwarded_port as _validate_forwarded_port
from app.core.forwarded_headers import is_trusted_proxy_source as is_trusted_proxy_source
from app.core.socket_peer import raw_socket_peer_host

_LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1", "[::1]"}
_TEST_SERVER_HOSTS = {"testserver", "testclient"}
FORWARDED_CHAIN_HEADER_NAMES = frozenset({"x-forwarded-for", "forwarded"})
_SINGLETON_CLIENT_IP_HEADER_NAMES = ("x-real-ip", "true-client-ip", "cf-connecting-ip")
_CONTAINER_BRIDGE_HOSTS = {
    "host.docker.internal",
    "gateway.docker.internal",
    "docker.for.mac.localhost",
    "docker.for.mac.host.internal",
    "0.250.250.254",
}
_CONTAINER_BRIDGE_SUFFIXES = (".orb.internal", ".orb.local")
_HOST_GATE_EXEMPT_PREFIXES = ("/v1", "/backend-api", "/muse", "/muse-api", "/health")
_FORWARDED_CLIENT_IP_HEADERS = FORWARDED_CHAIN_HEADER_NAMES | frozenset(_SINGLETON_CLIENT_IP_HEADER_NAMES)


def is_local_host(host: str | None) -> bool:
    if host is None:
        return False
    return host.strip().lower() in _LOCAL_HOSTS


def _combined_chain_header(headers: Mapping[str, str], header_name: str) -> str | None:
    if isinstance(headers, Headers):
        values = headers.getlist(header_name)
        return ",".join(values) if values else None
    return headers.get(header_name)


def _has_non_empty_header_value(headers: Mapping[str, str], header_name: str) -> bool:
    if isinstance(headers, Headers):
        return any((value.strip() for value in headers.getlist(header_name)))
    value = headers.get(header_name)
    return bool(value and value.strip())


def _resolve_proxy_header_consensus(
    headers: Mapping[str, str],
    socket_ip: str,
    trusted_proxy_networks: tuple[IPv4Network | IPv6Network, ...],
    allowed_header_names: frozenset[str],
) -> str | None:
    if isinstance(headers, Headers):
        for header_name in _SINGLETON_CLIENT_IP_HEADER_NAMES:
            if (
                header_name in allowed_header_names
                and len(headers.getlist(header_name)) > 1
                and _has_non_empty_header_value(headers, header_name)
            ):
                return None
    resolved_client_ips: list[str] = []
    if "x-forwarded-for" in allowed_header_names and _has_non_empty_header_value(headers, "x-forwarded-for"):
        forwarded_for = _combined_chain_header(headers, "x-forwarded-for")
        assert forwarded_for is not None
        try:
            resolved_client_ips.append(
                _resolve_client_ip_from_xff_chain(socket_ip, forwarded_for, trusted_proxy_networks)
            )
        except ValueError:
            return None
    for header_name in _SINGLETON_CLIENT_IP_HEADER_NAMES:
        if header_name not in allowed_header_names or not _has_non_empty_header_value(headers, header_name):
            continue
        forwarded_ip = headers.get(header_name)
        assert forwarded_ip is not None
        candidate = forwarded_ip.strip()
        if not _is_valid_ip(candidate):
            return None
        resolved_client_ips.append(candidate)
    if "forwarded" in allowed_header_names and _has_non_empty_header_value(headers, "forwarded"):
        forwarded = _combined_chain_header(headers, "forwarded")
        assert forwarded is not None
        try:
            resolved_client_ips.append(
                _resolve_client_ip_from_forwarded_chain(socket_ip, forwarded, trusted_proxy_networks)
            )
        except ValueError:
            return None
    if not resolved_client_ips:
        return None
    first_resolution = resolved_client_ips[0]
    first_address = ip_address(first_resolution)
    if any((ip_address(candidate) != first_address for candidate in resolved_client_ips[1:])):
        return None
    return first_resolution


def resolve_connection_client_ip(
    headers: Mapping[str, str],
    socket_ip: str | None,
    *,
    trust_proxy_headers: bool,
    trusted_proxy_networks: tuple[IPv4Network | IPv6Network, ...] = (),
    allowed_proxy_header_names: frozenset[str] | None = None,
    require_proxy_header_consensus: bool = False,
) -> str | None:
    if trust_proxy_headers and socket_ip and is_trusted_proxy_source(socket_ip, trusted_proxy_networks):
        allowed_header_names = (
            _FORWARDED_CLIENT_IP_HEADERS if allowed_proxy_header_names is None else allowed_proxy_header_names
        )
        if require_proxy_header_consensus:
            return _resolve_proxy_header_consensus(headers, socket_ip, trusted_proxy_networks, allowed_header_names)
        if isinstance(headers, Headers):
            for header_name in _SINGLETON_CLIENT_IP_HEADER_NAMES:
                if header_name in allowed_header_names and len(headers.getlist(header_name)) > 1:
                    return None
        forwarded_for = (
            _combined_chain_header(headers, "x-forwarded-for") if "x-forwarded-for" in allowed_header_names else None
        )
        if forwarded_for:
            try:
                resolved_from_chain = _resolve_client_ip_from_xff_chain(
                    socket_ip, forwarded_for, trusted_proxy_networks
                )
            except ValueError:
                return None
            if resolved_from_chain is not None:
                return resolved_from_chain
        for header_name in _SINGLETON_CLIENT_IP_HEADER_NAMES:
            if header_name not in allowed_header_names:
                continue
            forwarded_ip = headers.get(header_name)
            if forwarded_ip:
                candidate = forwarded_ip.strip()
                return candidate if _is_valid_ip(candidate) else None
        forwarded = _combined_chain_header(headers, "forwarded") if "forwarded" in allowed_header_names else None
        if forwarded:
            try:
                resolved = _resolve_client_ip_from_forwarded_chain(socket_ip, forwarded, trusted_proxy_networks)
            except ValueError:
                return None
            if resolved is not None:
                return resolved
        if _has_forwarded_client_ip_hint(headers):
            return None
        return socket_ip
    return socket_ip


def parse_trusted_proxy_networks(cidrs: list[str]) -> tuple[IPv4Network | IPv6Network, ...]:
    return tuple((ip_network(cidr, strict=False) for cidr in cidrs))


def _trusted_proxy_networks() -> tuple[IPv4Network | IPv6Network, ...]:
    settings = get_settings()
    return parse_trusted_proxy_networks(settings.firewall_trusted_proxy_cidrs)


def resolve_request_client_host(request: HTTPConnection) -> str | None:
    settings = get_settings()
    socket_ip = request.client.host if request.client else None
    return resolve_connection_client_ip(
        request.headers,
        socket_ip,
        trust_proxy_headers=settings.firewall_trust_proxy_headers,
        trusted_proxy_networks=_trusted_proxy_networks(),
    )


def _is_test_server_request(request: HTTPConnection) -> bool:
    server = request.scope.get("server")
    if not isinstance(server, tuple) or not server:
        return False
    host = server[0]
    if not isinstance(host, str):
        return False
    return host.strip().lower() in _TEST_SERVER_HOSTS


def _has_forwarded_client_ip_hint(headers: Mapping[str, str]) -> bool:
    if isinstance(headers, Headers):
        return any((value for header_name in _FORWARDED_CLIENT_IP_HEADERS for value in headers.getlist(header_name)))
    return any((headers.get(header_name) for header_name in _FORWARDED_CLIENT_IP_HEADERS))


def _parse_host_header_hostname(host_header: str | None) -> str | None:
    if host_header is None:
        return None
    value = host_header.strip()
    if not value:
        return None
    if value.startswith("["):
        closing = value.find("]")
        if closing != -1:
            return value[: closing + 1]
        return value
    if value.count(":") == 1:
        return value.split(":", 1)[0].strip()
    return value


def is_local_request(request: HTTPConnection) -> bool:
    if _is_test_server_request(request):
        return True
    settings = get_settings()
    trusted_proxy_networks = parse_trusted_proxy_networks(settings.firewall_trusted_proxy_cidrs)
    socket_ip = raw_socket_peer_host(request)
    client_host = resolve_connection_client_ip(
        request.headers,
        socket_ip,
        trust_proxy_headers=settings.firewall_trust_proxy_headers,
        trusted_proxy_networks=trusted_proxy_networks,
        require_proxy_header_consensus=True,
    )
    if not client_host:
        return False
    try:
        address = ip_address(client_host)
    except ValueError:
        return False
    if address.is_loopback:
        host_name = _parse_host_header_hostname(request.headers.get("host"))
        if settings.firewall_trust_proxy_headers:
            return (
                is_local_host(host_name)
                and socket_ip is not None
                and is_trusted_proxy_source(socket_ip, trusted_proxy_networks)
            )
        return is_local_host(host_name) and (not _has_forwarded_client_ip_hint(request.headers))
    return address.is_loopback


def is_container_bridge_host(host: str | None) -> bool:
    if host is None:
        return False
    name = host.strip().lower().rstrip(".")
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    return name in _CONTAINER_BRIDGE_HOSTS or name.endswith(_CONTAINER_BRIDGE_SUFFIXES)


def _is_host_gate_exempt_path(path: str) -> bool:
    return any((path == prefix or path.startswith(f"{prefix}/") for prefix in _HOST_GATE_EXEMPT_PREFIXES))


def is_untrusted_loopback_host_request(request: HTTPConnection) -> bool:
    if _is_host_gate_exempt_path(request.scope.get("path", "")) or _is_test_server_request(request):
        return False
    socket_ip = request.client.host if request.client else None
    try:
        if socket_ip is None or not ip_address(socket_ip).is_loopback:
            return False
    except ValueError:
        return False
    host_name = _parse_host_header_hostname(request.headers.get("host"))
    if is_container_bridge_host(host_name):
        return True
    client_host = resolve_request_client_host(request)
    try:
        forwarded_remote = client_host is not None and (not ip_address(client_host).is_loopback)
    except ValueError:
        forwarded_remote = False
    return not forwarded_remote and (not is_local_host(host_name))


def is_unauthenticated_client_allowed(request: HTTPConnection) -> bool:
    client_host = resolve_request_client_host(request)
    if client_host is None:
        return False
    try:
        client_ip = ip_address(client_host)
    except ValueError:
        return False
    configured_cidrs = get_settings().proxy_unauthenticated_client_cidrs
    return any((client_ip in ip_network(cidr, strict=False) for cidr in configured_cidrs))


def is_trusted_client(request: HTTPConnection) -> bool:
    return is_local_request(request) or is_unauthenticated_client_allowed(request)
