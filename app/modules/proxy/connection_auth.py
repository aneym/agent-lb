from __future__ import annotations

from typing import cast

from fastapi import Request, WebSocket
from fastapi.responses import JSONResponse

from app.core.auth.dependencies import (
    select_member_key_authorization,
    select_proxy_authorization,
    validate_proxy_api_key_authorization,
)
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.errors import openai_error
from app.core.exceptions import ProxyAuthError
from app.core.middleware.api_firewall import resolve_connection_client_ip
from app.db.session import get_background_session
from app.modules.api_keys.service_data import ApiKeyData
from app.modules.firewall.repository import FirewallRepository
from app.modules.firewall.service import FirewallRepositoryPort, FirewallService
from app.modules.proxy.api_dependencies import proxy_dependencies


async def validate_proxy_api_key_authorization_for_connection(
    authorization: str | None,
    connection: Request | WebSocket,
) -> ApiKeyData | None:
    try:
        return await validate_proxy_api_key_authorization(authorization, request=connection)
    except TypeError as exc:
        if not auth_override_rejects_request_keyword(exc):
            raise
    return await validate_proxy_api_key_authorization(authorization)


def auth_override_rejects_request_keyword(exc: TypeError) -> bool:
    message = str(exc)
    return "unexpected keyword argument 'request'" in message


async def validate_proxy_websocket_request(
    websocket: WebSocket,
) -> tuple[ApiKeyData | None, JSONResponse | None]:
    denial = await websocket_firewall_denial_response(websocket)
    if denial is not None:
        return None, denial
    try:
        api_key = await validate_proxy_api_key_authorization_for_connection(
            select_proxy_authorization(websocket.headers.get("authorization"), websocket.headers.get("x-api-key")),
            websocket,
        )
    except ProxyAuthError as exc:
        return None, JSONResponse(
            status_code=exc.status_code,
            content=openai_error(exc.code, exc.message, error_type=exc.error_type),
        )
    return api_key, None


async def validate_internal_bridge_api_key(
    request: Request,
) -> tuple[ApiKeyData | None, JSONResponse | None]:
    dashboard_settings = await get_settings_cache().get()
    try:
        if not dashboard_settings.api_key_auth_enabled:
            member_authorization = select_member_key_authorization(request.headers.get("authorization"), None)
            if member_authorization is None:
                return None, None
            return await validate_proxy_api_key_authorization(member_authorization), None
        api_key = await validate_proxy_api_key_authorization_for_connection(
            request.headers.get("authorization"),
            request,
        )
    except ProxyAuthError as exc:
        return None, JSONResponse(
            status_code=exc.status_code,
            content=openai_error(exc.code, exc.message, error_type=exc.error_type),
        )
    return api_key, None


async def websocket_firewall_denial_response(websocket: WebSocket) -> JSONResponse | None:
    settings = get_settings()
    client_ip = resolve_connection_client_ip(
        websocket.headers,
        websocket.client.host if websocket.client else None,
        trust_proxy_headers=settings.firewall_trust_proxy_headers,
        trusted_proxy_networks=proxy_dependencies.parse_trusted_proxy_networks(settings.firewall_trusted_proxy_cidrs),
    )
    async with get_background_session() as session:
        repository = cast(FirewallRepositoryPort, FirewallRepository(session))
        service = FirewallService(repository)
        if await service.is_ip_allowed(client_ip):
            return None
    return JSONResponse(
        status_code=403,
        content=openai_error("ip_forbidden", "Access denied for client IP", error_type="access_error"),
    )
