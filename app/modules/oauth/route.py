from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clients.oauth import OAuthError
from app.core.upstream_proxy import ResolvedUpstreamRoute, UpstreamProxyRouteError, resolve_upstream_route
from app.db.models import AccountProxyBinding
from app.db.session import get_background_session


async def _has_active_proxy_bindings(session: AsyncSession) -> bool:
    try:
        result = await session.execute(
            select(AccountProxyBinding.id).where(AccountProxyBinding.is_active.is_(True)).limit(1)
        )
        return result.scalar_one_or_none() is not None
    except OperationalError:
        return False


async def _oauth_route() -> ResolvedUpstreamRoute | None:
    async with get_background_session() as session:
        strict = await _has_active_proxy_bindings(session)
        try:
            return await resolve_upstream_route(
                session, account_id=None, operation="oauth", scope="bootstrap", strict=strict or None
            )
        except UpstreamProxyRouteError as exc:
            raise OAuthError(exc.reason, str(exc), status_code=502) from exc
