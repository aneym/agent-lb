from __future__ import annotations

import sys
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, cast

from cryptography.fernet import InvalidToken
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.crypto import TokenEncryptor
from app.core.upstream_proxy.types import ResolvedProxyEndpoint, ResolvedUpstreamRoute
from app.db.models import DashboardSettings
from app.db.request_models import AccountProxyBinding, ProxyEndpoint, ProxyPool, ProxyPoolMember

_ACCOUNT_BOUND_MODE = "account_bound"
_DEFAULT_POOL_MODE = "default_pool"
_SUPPORTED_SCHEMES = frozenset({"http", "https", "socks5", "socks5h"})
ROUTE_CACHE_MISS = object()


@dataclass(slots=True)
class _CachedRoute:
    value: ResolvedUpstreamRoute | None
    expires_at: float


_route_cache_ttl_seconds = 0.0 if "pytest" in sys.modules else 5.0
_route_cache: dict[tuple[str | None, str, bool | None], _CachedRoute] = {}
_route_cache_generation = 0


def invalidate_upstream_route_cache() -> None:
    global _route_cache_generation
    _route_cache_generation += 1
    _route_cache.clear()


def get_cached_upstream_route(
    account_id: str | None,
    *,
    scope: str = "account",
    strict: bool | None = None,
) -> ResolvedUpstreamRoute | None | object:
    if _route_cache_ttl_seconds <= 0.0:
        return ROUTE_CACHE_MISS
    key = (account_id, scope, strict)
    entry = _route_cache.get(key)
    if entry is None:
        return ROUTE_CACHE_MISS
    if time.monotonic() >= entry.expires_at:
        _route_cache.pop(key, None)
        return ROUTE_CACHE_MISS
    return entry.value


def _store_cached_upstream_route(
    key: tuple[str | None, str, bool | None],
    value: ResolvedUpstreamRoute | None,
    *,
    generation: int,
) -> None:
    if _route_cache_ttl_seconds <= 0.0 or generation != _route_cache_generation:
        return
    _route_cache[key] = _CachedRoute(value=value, expires_at=time.monotonic() + _route_cache_ttl_seconds)


async def resolve_upstream_route_cached(
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    resolver: Callable[..., Awaitable[ResolvedUpstreamRoute | None]],
    *,
    account_id: str | None,
    operation: str,
    scope: str = "account",
    strict: bool | None = None,
    encryptor: TokenEncryptor | None = None,
) -> ResolvedUpstreamRoute | None:
    cached = get_cached_upstream_route(account_id, scope=scope, strict=strict)
    if cached is not ROUTE_CACHE_MISS:
        return cast(ResolvedUpstreamRoute | None, cached)
    kwargs: dict[str, Any] = {
        "account_id": account_id,
        "operation": operation,
        "scope": scope,
        "encryptor": encryptor,
    }
    if strict is not None:
        kwargs["strict"] = strict
    async with session_factory() as session:
        return await resolver(session, **kwargs)


class UpstreamProxyRouteError(RuntimeError):
    def __init__(self, reason: str, *, account_id: str | None = None, pool_id: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.account_id = account_id
        self.pool_id = pool_id


async def resolve_upstream_route(
    session: AsyncSession,
    *,
    account_id: str | None,
    operation: str,
    scope: str = "account",
    strict: bool | None = None,
    encryptor: TokenEncryptor | None = None,
) -> ResolvedUpstreamRoute | None:
    del operation
    cache_key = (account_id, scope, strict)
    cached = get_cached_upstream_route(account_id, scope=scope, strict=strict)
    if cached is not ROUTE_CACHE_MISS:
        return cast(ResolvedUpstreamRoute | None, cached)
    generation = _route_cache_generation
    try:
        binding = await _lookup_account_binding(session, account_id) if account_id else None
    except OperationalError as exc:
        if strict is True or not _is_missing_upstream_proxy_schema(exc):
            raise
        return None
    if binding is not None:
        resolved = await _resolve_pool(
            session,
            pool_id=binding.pool_id,
            mode=_ACCOUNT_BOUND_MODE,
            account_id=account_id,
            encryptor=encryptor,
        )
        _store_cached_upstream_route(cache_key, resolved, generation=generation)
        return resolved

    try:
        settings = await _lookup_dashboard_settings(session)
    except OperationalError as exc:
        if strict is True or not _is_missing_upstream_proxy_schema(exc):
            raise
        return None
    strict_enabled = settings.upstream_proxy_routing_enabled if settings is not None else False
    if strict is not None:
        strict_enabled = strict
    if not strict_enabled:
        _store_cached_upstream_route(cache_key, None, generation=generation)
        return None
    pool_id = settings.upstream_proxy_default_pool_id if settings is not None else None
    if not pool_id:
        raise UpstreamProxyRouteError("default_pool_unconfigured", account_id=account_id)
    if scope not in {"account", "service", "bootstrap"}:
        raise UpstreamProxyRouteError("unsupported_route_scope", account_id=account_id, pool_id=pool_id)
    resolved = await _resolve_pool(
        session,
        pool_id=pool_id,
        mode=_DEFAULT_POOL_MODE,
        account_id=account_id,
        encryptor=encryptor,
    )
    _store_cached_upstream_route(cache_key, resolved, generation=generation)
    return resolved


def _is_missing_upstream_proxy_schema(exc: OperationalError) -> bool:
    message = str(exc.orig).lower() if getattr(exc, "orig", None) is not None else str(exc).lower()
    return (
        "no such table: account_proxy_bindings" in message
        or "no such column: dashboard_settings.upstream_proxy" in message
    )


async def _lookup_account_binding(session: AsyncSession, account_id: str | None) -> AccountProxyBinding | None:
    if not account_id:
        return None
    result = await session.execute(
        select(AccountProxyBinding)
        .where(AccountProxyBinding.account_id == account_id, AccountProxyBinding.is_active.is_(True))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _lookup_dashboard_settings(session: AsyncSession) -> DashboardSettings | None:
    result = await session.execute(select(DashboardSettings).where(DashboardSettings.id == 1).limit(1))
    return result.scalar_one_or_none()


async def _resolve_pool(
    session: AsyncSession,
    *,
    pool_id: str,
    mode: str,
    account_id: str | None,
    encryptor: TokenEncryptor | None,
) -> ResolvedUpstreamRoute:
    pool = await session.get(ProxyPool, pool_id)
    if pool is None or not pool.is_active:
        raise UpstreamProxyRouteError("pool_unavailable", account_id=account_id, pool_id=pool_id)
    result = await session.execute(
        select(ProxyPoolMember)
        .options(selectinload(ProxyPoolMember.endpoint))
        .where(ProxyPoolMember.pool_id == pool_id, ProxyPoolMember.is_active.is_(True))
        .order_by(ProxyPoolMember.sort_order.asc(), ProxyPoolMember.id.asc())
    )
    endpoints = [
        _resolve_endpoint(member.endpoint, encryptor=encryptor)
        for member in result.scalars().all()
        if member.endpoint is not None and member.endpoint.is_active
    ]
    if not endpoints:
        raise UpstreamProxyRouteError("pool_has_no_active_endpoints", account_id=account_id, pool_id=pool_id)
    return ResolvedUpstreamRoute(mode=mode, pool_id=pool_id, endpoint=endpoints[0], fallbacks=tuple(endpoints[1:]))


def _resolve_endpoint(endpoint: ProxyEndpoint, *, encryptor: TokenEncryptor | None) -> ResolvedProxyEndpoint:
    scheme = endpoint.scheme.lower().strip()
    if scheme not in _SUPPORTED_SCHEMES:
        raise UpstreamProxyRouteError("unsupported_proxy_scheme")
    password: str | None = None
    if endpoint.password_encrypted is not None:
        try:
            password = (encryptor or TokenEncryptor()).decrypt(endpoint.password_encrypted)
        except InvalidToken as exc:
            raise UpstreamProxyRouteError("proxy_credentials_undecryptable") from exc
    return ResolvedProxyEndpoint(
        id=endpoint.id,
        scheme=scheme,
        host=endpoint.host,
        port=endpoint.port,
        username=endpoint.username,
        password=password,
    )
