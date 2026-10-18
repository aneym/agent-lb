from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.crypto import TokenEncryptor
from app.core.exceptions import DashboardBadRequestError
from app.db.models import Account, AccountProxyBinding, ProxyEndpoint, ProxyPool, ProxyPoolMember
from app.dependencies import SettingsContext, get_settings_context
from app.modules.settings.schemas import (
    AccountProxyBindingRequest,
    AccountProxyBindingResponse,
    UpstreamProxyAdminResponse,
    UpstreamProxyEndpointCreateRequest,
    UpstreamProxyEndpointResponse,
    UpstreamProxyPoolCreateRequest,
    UpstreamProxyPoolMemberRequest,
    UpstreamProxyPoolResponse,
)

router = APIRouter()


@router.get("/upstream-proxy", response_model=UpstreamProxyAdminResponse)
async def get_upstream_proxy_admin(
    context: SettingsContext = Depends(get_settings_context),
) -> UpstreamProxyAdminResponse:
    settings = await context.repository.get_or_create()
    endpoint_rows = (await context.session.execute(select(ProxyEndpoint).order_by(ProxyEndpoint.name.asc()))).scalars()
    pool_rows = (await context.session.execute(select(ProxyPool).order_by(ProxyPool.name.asc()))).scalars().all()
    member_rows = (
        await context.session.execute(select(ProxyPoolMember).order_by(ProxyPoolMember.sort_order.asc()))
    ).scalars()
    bindings = (
        (await context.session.execute(select(AccountProxyBinding).order_by(AccountProxyBinding.account_id.asc())))
        .scalars()
        .all()
    )
    endpoint_ids_by_pool: dict[str, list[str]] = {}
    for member in member_rows:
        endpoint_ids_by_pool.setdefault(member.pool_id, []).append(member.endpoint_id)
    return UpstreamProxyAdminResponse(
        routing_enabled=settings.upstream_proxy_routing_enabled,
        default_pool_id=settings.upstream_proxy_default_pool_id,
        endpoints=[_proxy_endpoint_response(row) for row in endpoint_rows],
        pools=[
            UpstreamProxyPoolResponse(
                id=row.id,
                name=row.name,
                is_active=row.is_active,
                endpoint_ids=endpoint_ids_by_pool.get(row.id, []),
            )
            for row in pool_rows
        ],
        bindings=[
            AccountProxyBindingResponse(account_id=row.account_id, pool_id=row.pool_id, is_active=row.is_active)
            for row in bindings
        ],
    )


@router.post("/upstream-proxy/endpoints", response_model=UpstreamProxyEndpointResponse)
async def create_upstream_proxy_endpoint(
    payload: UpstreamProxyEndpointCreateRequest,
    context: SettingsContext = Depends(get_settings_context),
) -> UpstreamProxyEndpointResponse:
    encryptor = TokenEncryptor()
    row = ProxyEndpoint(
        name=payload.name,
        scheme=payload.scheme,
        host=payload.host,
        port=payload.port,
        username=payload.username,
        password_encrypted=encryptor.encrypt(payload.password) if payload.password else None,
        is_active=payload.is_active,
    )
    context.session.add(row)
    await context.session.commit()
    await context.session.refresh(row)
    return _proxy_endpoint_response(row)


@router.post("/upstream-proxy/pools", response_model=UpstreamProxyPoolResponse)
async def create_upstream_proxy_pool(
    payload: UpstreamProxyPoolCreateRequest,
    context: SettingsContext = Depends(get_settings_context),
) -> UpstreamProxyPoolResponse:
    endpoint_ids = list(dict.fromkeys(payload.endpoint_ids))
    await _validate_proxy_endpoint_ids(context, endpoint_ids)
    pool = ProxyPool(name=payload.name, is_active=payload.is_active)
    context.session.add(pool)
    await context.session.flush()
    for sort_order, endpoint_id in enumerate(endpoint_ids):
        context.session.add(ProxyPoolMember(pool_id=pool.id, endpoint_id=endpoint_id, sort_order=sort_order))
    try:
        await context.session.commit()
    except IntegrityError as exc:
        await context.session.rollback()
        if _is_missing_proxy_endpoint_error(exc):
            raise DashboardBadRequestError("Proxy endpoint not found", code="proxy_endpoint_not_found")
        raise
    await context.session.refresh(pool)
    return UpstreamProxyPoolResponse(
        id=pool.id,
        name=pool.name,
        is_active=pool.is_active,
        endpoint_ids=endpoint_ids,
    )


@router.post("/upstream-proxy/pools/{pool_id}/members", response_model=UpstreamProxyPoolResponse)
async def add_upstream_proxy_pool_member(
    pool_id: str,
    payload: UpstreamProxyPoolMemberRequest,
    context: SettingsContext = Depends(get_settings_context),
) -> UpstreamProxyPoolResponse:
    pool = await context.session.get(ProxyPool, pool_id)
    if pool is None:
        raise DashboardBadRequestError("Proxy pool not found", code="proxy_pool_not_found")
    await _validate_proxy_endpoint_ids(context, [payload.endpoint_id])
    await _validate_proxy_pool_member_is_unique(context, pool_id=pool_id, endpoint_id=payload.endpoint_id)
    context.session.add(
        ProxyPoolMember(
            pool_id=pool_id,
            endpoint_id=payload.endpoint_id,
            sort_order=payload.sort_order,
            weight=payload.weight,
            is_active=payload.is_active,
        )
    )
    try:
        await context.session.commit()
    except IntegrityError as exc:
        await context.session.rollback()
        if _is_missing_proxy_endpoint_error(exc):
            raise DashboardBadRequestError("Proxy endpoint not found", code="proxy_endpoint_not_found")
        if _is_duplicate_proxy_pool_member_error(exc):
            raise _duplicate_proxy_pool_member_error()
        raise
    endpoint_ids = (
        (
            await context.session.execute(
                select(ProxyPoolMember.endpoint_id)
                .where(ProxyPoolMember.pool_id == pool_id)
                .order_by(ProxyPoolMember.sort_order.asc())
            )
        )
        .scalars()
        .all()
    )
    return UpstreamProxyPoolResponse(
        id=pool.id,
        name=pool.name,
        is_active=pool.is_active,
        endpoint_ids=list(endpoint_ids),
    )


async def _validate_proxy_endpoint_ids(context: SettingsContext, endpoint_ids: list[str]) -> None:
    if not endpoint_ids:
        return
    existing_ids = set(
        (await context.session.execute(select(ProxyEndpoint.id).where(ProxyEndpoint.id.in_(endpoint_ids))))
        .scalars()
        .all()
    )
    missing_ids = [endpoint_id for endpoint_id in endpoint_ids if endpoint_id not in existing_ids]
    if missing_ids:
        raise DashboardBadRequestError(
            f"Proxy endpoint not found: {', '.join(missing_ids)}",
            code="proxy_endpoint_not_found",
        )


async def _validate_proxy_pool_member_is_unique(
    context: SettingsContext,
    *,
    pool_id: str,
    endpoint_id: str,
) -> None:
    existing_id = (
        await context.session.execute(
            select(ProxyPoolMember.id)
            .where(ProxyPoolMember.pool_id == pool_id, ProxyPoolMember.endpoint_id == endpoint_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing_id is not None:
        raise _duplicate_proxy_pool_member_error()


async def validate_proxy_pool_id(context: SettingsContext, pool_id: str | None) -> None:
    if pool_id is None:
        return
    if await context.session.get(ProxyPool, pool_id) is None:
        raise DashboardBadRequestError("Proxy pool not found", code="proxy_pool_not_found")


async def _validate_account_id(context: SettingsContext, account_id: str) -> None:
    if await context.session.get(Account, account_id) is None:
        raise DashboardBadRequestError("Account not found", code="account_not_found")


def _is_missing_proxy_endpoint_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "foreign key" in message or "fk constraint" in message or "violates foreign key constraint" in message


def _is_duplicate_proxy_pool_member_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "uq_proxy_pool_members_pool_endpoint" in message
        or ("proxy_pool_members" in message and "unique" in message)
        or ("proxy_pool_members.pool_id" in message and "proxy_pool_members.endpoint_id" in message)
    )


def _duplicate_proxy_pool_member_error() -> DashboardBadRequestError:
    return DashboardBadRequestError(
        "Proxy endpoint is already a member of this pool",
        code="proxy_pool_member_duplicate",
    )


@router.put("/upstream-proxy/accounts/{account_id}/binding", response_model=AccountProxyBindingResponse)
async def put_account_proxy_binding(
    account_id: str,
    payload: AccountProxyBindingRequest,
    context: SettingsContext = Depends(get_settings_context),
) -> AccountProxyBindingResponse:
    await _validate_account_id(context, account_id)
    await validate_proxy_pool_id(context, payload.pool_id)
    row = (
        (
            await context.session.execute(
                select(AccountProxyBinding).where(AccountProxyBinding.account_id == account_id).limit(1)
            )
        )
        .scalars()
        .one_or_none()
    )
    if row is None:
        row = AccountProxyBinding(account_id=account_id, pool_id=payload.pool_id, is_active=payload.is_active)
        context.session.add(row)
    else:
        row.pool_id = payload.pool_id
        row.is_active = payload.is_active
    await context.session.commit()
    await context.session.refresh(row)
    return AccountProxyBindingResponse(account_id=row.account_id, pool_id=row.pool_id, is_active=row.is_active)


def _proxy_endpoint_response(row: ProxyEndpoint) -> UpstreamProxyEndpointResponse:
    return UpstreamProxyEndpointResponse(
        id=row.id,
        name=row.name,
        scheme=row.scheme,
        host=row.host,
        port=row.port,
        username=row.username,
        is_active=row.is_active,
    )
