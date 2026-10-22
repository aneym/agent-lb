from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from collections.abc import Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from functools import lru_cache
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address, ip_network
from typing import Protocol

from sqlalchemy import select

from app.core.config.settings import Settings, get_settings, identity_handle
from app.core.request_locality import parse_trusted_proxy_networks, resolve_connection_client_ip
from app.db.access_models import TeamMember
from app.db.session import get_background_session

logger = logging.getLogger(__name__)


USER_MEMBER = "member"
USER_OWNER_MACHINE = "owner-machine"
USER_UNKNOWN = "unknown"

MACHINE_LOCAL = "local"
MACHINE_TAILNET = "tailnet"
MACHINE_FUNNEL = "funnel"
MACHINE_REMOTE = "remote"
MACHINE_CLAIMED = "claimed"

INTERNAL = "internal"

TAILNET_UNKNOWN = "tailnet-unknown"
FUNNEL_MACHINE = "public"
USER_MAX_LENGTH = 32
MACHINE_MAX_LENGTH = 48

_TAILNET_NETWORKS = (ip_network("100.64.0.0/10"), ip_network("fd7a:115c:a1e0::/48"))
_LOOPBACK_PROXY_CIDRS = ("127.0.0.0/8", "::1/128")
_FUNNEL_HEADER = "tailscale-funnel-request"
_CLAIM_HEADER = "x-agent-lb-machine"
_CLAIM_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,47}")


_OWNER_MACHINE_SOURCES = frozenset({MACHINE_LOCAL, MACHINE_TAILNET, MACHINE_CLAIMED})
_MEMBER_CACHE_TTL_SECONDS = 60.0
_MEMBER_FAILURE_CACHE_TTL_SECONDS = 10.0
_MEMBER_CACHE_MAX_ENTRIES = 512
_MEMBER_LOOKUP_TIMEOUT_SECONDS = 0.75
_WARNING_INTERVAL_SECONDS = 3600.0


@dataclass(frozen=True, slots=True)
class RequestIdentity:
    caller_user: str | None
    caller_user_source: str | None
    caller_machine: str | None
    caller_machine_source: str | None


DISABLED_IDENTITY = RequestIdentity(None, None, None, None)

_UNRESOLVED_IDENTITY = RequestIdentity(USER_UNKNOWN, USER_UNKNOWN, MACHINE_REMOTE, MACHINE_REMOTE)


UNATTRIBUTED_FORWARD_IDENTITY = _UNRESOLVED_IDENTITY


class NodeLookup(Protocol):
    def get(self, ip: str, /) -> str | None: ...


class _NoNodes:
    def get(self, ip: str, /) -> str | None:
        return None


_node_lookup: NodeLookup = _NoNodes()
_request_identity: ContextVar[RequestIdentity | None] = ContextVar("request_identity", default=None)
_member_names: dict[str, tuple[float, str | None, float]] = {}
_member_lookups: dict[str, asyncio.Task[str | None]] = {}
_last_warning_at: dict[str, float] = {}


def set_node_lookup(lookup: NodeLookup | None) -> None:

    global _node_lookup
    _node_lookup = lookup if lookup is not None else _NoNodes()


def get_request_identity() -> RequestIdentity | None:
    return _request_identity.get()


def set_request_identity(identity: RequestIdentity | None) -> Token[RequestIdentity | None]:
    return _request_identity.set(identity)


def reset_request_identity(token: Token[RequestIdentity | None]) -> None:
    _request_identity.reset(token)


def resolve_scope_identity(headers: Mapping[str, str], socket_ip: str | None) -> RequestIdentity:

    try:
        return resolve_identity(headers, socket_ip)
    except Exception:
        _warn_hourly("resolve", "request identity resolution failed; recording unknown", exc_info=True)
        return _UNRESOLVED_IDENTITY


def identity_for_log(identity: RequestIdentity | None = None) -> RequestIdentity:

    settings = get_settings()
    if not settings.identity_enabled:
        return DISABLED_IDENTITY
    if identity is not None:
        return identity
    return get_request_identity() or internal_identity(settings)


def internal_identity(settings: Settings | None = None) -> RequestIdentity:

    settings = settings if settings is not None else get_settings()
    if not settings.identity_enabled:
        return DISABLED_IDENTITY
    return RequestIdentity(INTERNAL, INTERNAL, local_machine_handle(settings), INTERNAL)


def resolve_identity(
    headers: Mapping[str, str],
    socket_ip: str | None,
    *,
    member_id: str | None = None,
    member_name: str | None = None,
    node_cache: NodeLookup | None = None,
    settings: Settings | None = None,
) -> RequestIdentity:

    settings = settings if settings is not None else get_settings()
    if not settings.identity_enabled:
        return DISABLED_IDENTITY
    nodes = node_cache if node_cache is not None else _node_lookup
    first_hand = _seen_first_hand(headers, socket_ip, settings)
    machine, machine_source = _resolve_machine(headers, socket_ip, settings, nodes, first_hand=first_hand)
    owner_machines = set(settings.identity_owner_machines)
    if (
        first_hand
        and machine_source in _OWNER_MACHINE_SOURCES
        and machine in owner_machines
        and (machine_source != MACHINE_CLAIMED or local_machine_handle(settings) in owner_machines)
    ):
        owner = _handle(settings.identity_owner, USER_MAX_LENGTH) or "owner"
        identity = RequestIdentity(owner, USER_OWNER_MACHINE, machine, machine_source)
    else:
        identity = RequestIdentity(USER_UNKNOWN, USER_UNKNOWN, machine, machine_source)
    return apply_member(identity, member_id=member_id, member_name=member_name)


def apply_member(identity: RequestIdentity, *, member_id: str | None, member_name: str | None) -> RequestIdentity:

    if not member_id or identity.caller_user_source in (None, INTERNAL):
        return identity
    handle = _member_handle(member_name)
    if handle is None:
        return replace(identity, caller_user=USER_UNKNOWN, caller_user_source=USER_UNKNOWN)
    return replace(identity, caller_user=handle, caller_user_source=USER_MEMBER)


def local_machine_handle(settings: Settings) -> str:
    return _handle(settings.identity_local_machine, MACHINE_MAX_LENGTH) or MACHINE_LOCAL


def _seen_first_hand(headers: Mapping[str, str], socket_ip: str | None, settings: Settings) -> bool:

    configured = resolve_connection_client_ip(
        headers,
        socket_ip,
        trust_proxy_headers=settings.firewall_trust_proxy_headers,
        trusted_proxy_networks=_trusted_proxy_networks(tuple(settings.firewall_trusted_proxy_cidrs)),
    )
    loopback_only = resolve_connection_client_ip(
        headers,
        socket_ip,
        trust_proxy_headers=settings.firewall_trust_proxy_headers,
        trusted_proxy_networks=_trusted_proxy_networks(_LOOPBACK_PROXY_CIDRS),
    )
    return configured == loopback_only


def _resolve_machine(
    headers: Mapping[str, str],
    socket_ip: str | None,
    settings: Settings,
    nodes: NodeLookup,
    *,
    first_hand: bool,
) -> tuple[str, str]:

    if headers.get(_FUNNEL_HEADER) is not None:
        return FUNNEL_MACHINE, MACHINE_FUNNEL
    address = _client_address(
        resolve_connection_client_ip(
            headers,
            socket_ip,
            trust_proxy_headers=settings.firewall_trust_proxy_headers,
            trusted_proxy_networks=_trusted_proxy_networks(tuple(settings.firewall_trusted_proxy_cidrs)),
        )
    )
    if address is None:
        return MACHINE_REMOTE, MACHINE_REMOTE
    if address.is_loopback:
        peer = _client_address(socket_ip)
        if peer is None or not peer.is_loopback or not first_hand:
            return MACHINE_REMOTE, MACHINE_REMOTE

        forwarding_headers = ("x-forwarded-for", "forwarded", "x-real-ip", "true-client-ip", "cf-connecting-ip")
        if not settings.firewall_trust_proxy_headers and any(headers.get(name) for name in forwarding_headers):
            return MACHINE_REMOTE, MACHINE_REMOTE
        claim = (headers.get(_CLAIM_HEADER) or "").strip()
        if _CLAIM_PATTERN.fullmatch(claim):
            return claim, MACHINE_CLAIMED
        return local_machine_handle(settings), MACHINE_LOCAL
    if any(address in network for network in _TAILNET_NETWORKS):
        return _tailnet_machine(str(address), settings, nodes), MACHINE_TAILNET
    return MACHINE_REMOTE, MACHINE_REMOTE


def _tailnet_machine(ip: str, settings: Settings, nodes: NodeLookup) -> str:
    try:
        node = _handle(nodes.get(ip), MACHINE_MAX_LENGTH)
    except Exception:
        _warn_hourly("nodes", "tailnet node lookup failed; recording tailnet-unknown", exc_info=True)
        node = None
    if node is None:
        return TAILNET_UNKNOWN
    return _handle(settings.identity_machine_aliases.get(node, node), MACHINE_MAX_LENGTH) or TAILNET_UNKNOWN


def _client_address(client_ip: str | None) -> IPv4Address | IPv6Address | None:
    if not client_ip:
        return None
    try:
        address = ip_address(client_ip)
    except ValueError:
        return None
    return address


@lru_cache(maxsize=8)
def _trusted_proxy_networks(cidrs: tuple[str, ...]) -> tuple[IPv4Network | IPv6Network, ...]:
    return parse_trusted_proxy_networks(list(cidrs))


def _member_handle(name: str | None) -> str | None:

    return _handle(name, USER_MAX_LENGTH) if name and "@" not in unicodedata.normalize("NFKC", name) else None


def _handle(value: str | None, limit: int) -> str | None:
    return identity_handle(value, limit)


async def attach_member(identity: RequestIdentity | None, member_id: str | None) -> RequestIdentity | None:

    if identity is None or not member_id or identity.caller_user_source in (None, INTERNAL):
        return identity
    return apply_member(identity, member_id=member_id, member_name=await _member_name(member_id))


async def _member_name(member_id: str) -> str | None:
    now = time.monotonic()
    cached = _member_names.get(member_id)
    if cached is not None and now - cached[0] < cached[2]:
        return cached[1]
    task = _member_lookups.get(member_id)
    if task is None:
        task = asyncio.create_task(_lookup_member_name(member_id))
        _member_lookups[member_id] = task
        task.add_done_callback(
            lambda completed: (
                _member_lookups.pop(member_id, None) if _member_lookups.get(member_id) is completed else None
            )
        )

    return await asyncio.shield(task)


async def _lookup_member_name(member_id: str) -> str | None:
    try:
        async with asyncio.timeout(_MEMBER_LOOKUP_TIMEOUT_SECONDS):
            async with get_background_session() as session:
                name = await session.scalar(select(TeamMember.name).where(TeamMember.id == member_id))
    except Exception:
        _warn_hourly("member", "team member lookup failed; recording unknown user", exc_info=True)
        handle = None
        ttl = _MEMBER_FAILURE_CACHE_TTL_SECONDS
    else:
        handle = _member_handle(name)
        ttl = _MEMBER_CACHE_TTL_SECONDS
    if len(_member_names) >= _MEMBER_CACHE_MAX_ENTRIES:
        _member_names.clear()
    _member_names[member_id] = (time.monotonic(), handle, ttl)
    return handle


def _warn_hourly(key: str, message: str, *, exc_info: bool = False) -> None:
    now = time.monotonic()
    last = _last_warning_at.get(key)
    if last is not None and now - last < _WARNING_INTERVAL_SECONDS:
        return
    _last_warning_at[key] = now
    logger.warning(message, exc_info=exc_info)
