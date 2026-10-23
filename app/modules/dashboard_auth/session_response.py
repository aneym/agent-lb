from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.auth.dashboard_access import ADMIN_PERMISSIONS, GUEST_PERMISSIONS, DashboardRole
from app.core.auth.dashboard_mode import DashboardAuthMode, get_dashboard_request_auth, password_management_enabled
from app.core.auth.dashboard_session_ttl import resolve_dashboard_session_ttl_seconds
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.exceptions import DashboardValidationError
from app.modules.dashboard_auth.schemas import DashboardAuthSessionResponse
from app.modules.dashboard_auth.service import DASHBOARD_SESSION_COOKIE, get_dashboard_session_store

_MAX_PASSWORD_BYTES = 72


def _validate_password_length(password: str) -> None:
    if len(password) < 8:
        raise DashboardValidationError("Password must be at least 8 characters")
    if len(password.encode("utf-8")) > _MAX_PASSWORD_BYTES:
        raise DashboardValidationError(
            f"Password must be at most {_MAX_PASSWORD_BYTES} bytes when encoded as UTF-8. Note that multi-byte characters (e.g. emoji, non-ASCII letters) count for more than one byte.",
            code="password_too_long",
        )


def _set_session_cookie(response: JSONResponse, session_id: str, request: Request, *, max_age_seconds: int) -> None:
    response.set_cookie(
        key=DASHBOARD_SESSION_COOKIE,
        value=session_id,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=max_age_seconds,
        path="/",
    )


def _decorate_session_response(
    response: DashboardAuthSessionResponse,
    *,
    request: Request,
    force_authenticated: bool = False,
    password_session_id: str | None = None,
) -> DashboardAuthSessionResponse:
    request_auth = get_dashboard_request_auth(request)
    auth_mode = get_settings().dashboard_auth_mode
    store = get_dashboard_session_store()
    sid = password_session_id or request.cookies.get(DASHBOARD_SESSION_COOKIE)
    session_state = store.get(sid) if sid else None
    session_role = getattr(session_state, "role", DashboardRole.ADMIN)
    has_pwd = session_state is not None and session_role == DashboardRole.ADMIN and session_state.password_verified
    totp_pending = (
        has_pwd and session_state is not None and response.totp_required_on_login and (not session_state.totp_verified)
    )
    fully_authorized = has_pwd and (not totp_pending) and response.password_required
    if request_auth is None:
        update: dict[str, object] = {
            "auth_mode": auth_mode,
            "password_management_enabled": password_management_enabled(auth_mode),
            "password_session_active": fully_authorized,
        }
        if (
            auth_mode == DashboardAuthMode.TRUSTED_HEADER
            and (not response.password_required)
            and (not response.totp_required_on_login)
        ):
            update["authenticated"] = False
        return response.model_copy(update=update)
    return response.model_copy(
        update={
            "authenticated": force_authenticated or response.authenticated,
            "totp_required_on_login": totp_pending,
            "auth_mode": request_auth.mode,
            "password_management_enabled": password_management_enabled(request_auth.mode),
            "password_session_active": fully_authorized,
            "role": DashboardRole.ADMIN,
            "permissions": sorted(ADMIN_PERMISSIONS),
        }
    )


async def _create_dashboard_session(
    request: Request,
    *,
    password_verified: bool,
    totp_verified: bool,
    role: DashboardRole = DashboardRole.ADMIN,
    guest_verified: bool = False,
) -> tuple[str, int]:
    settings = await get_settings_cache().get()
    ttl_seconds = resolve_dashboard_session_ttl_seconds(request, settings.dashboard_session_ttl_seconds)
    session_id = get_dashboard_session_store().create(
        password_verified=password_verified,
        totp_verified=totp_verified,
        ttl_seconds=ttl_seconds,
        role=role,
        guest_verified=guest_verified,
    )
    return (session_id, ttl_seconds)


def _session_client_key(request: Request, *, prefix: str) -> str:
    return f"{prefix}:{(request.client.host if request.client else 'unknown')}"


logger = logging.getLogger(__name__)


def _public_guest_response(response: DashboardAuthSessionResponse) -> DashboardAuthSessionResponse:
    return response.model_copy(
        update={
            "authenticated": True,
            "bootstrap_required": False,
            "bootstrap_token_configured": False,
            "role": DashboardRole.GUEST,
            "permissions": sorted(GUEST_PERMISSIONS),
            "guest_access_enabled": True,
            "guest_password_required": False,
            "password_session_active": False,
        }
    )


def _guest_login_required_response(response: DashboardAuthSessionResponse) -> DashboardAuthSessionResponse:
    return response.model_copy(
        update={
            "authenticated": False,
            "bootstrap_required": False,
            "bootstrap_token_configured": False,
            "role": DashboardRole.GUEST,
            "permissions": sorted(GUEST_PERMISSIONS),
            "guest_access_enabled": True,
            "guest_password_required": True,
            "password_session_active": False,
        }
    )
