from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.auth.dashboard_mode import DashboardAuthMode, get_dashboard_request_auth, password_management_enabled
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.exceptions import DashboardAuthError, DashboardBadRequestError, DashboardValidationError
from app.dependencies import DashboardAuthContext
from app.modules.dashboard_auth.schemas import DashboardAuthSessionResponse
from app.modules.dashboard_auth.service import DASHBOARD_SESSION_COOKIE, get_dashboard_session_store


def session_client_key(request: Request, *, prefix: str) -> str:
    return f"{prefix}:{request.client.host if request.client else 'unknown'}"


async def create_dashboard_session(*, password_verified: bool, totp_verified: bool) -> tuple[str, int]:
    settings = await get_settings_cache().get()
    ttl_seconds = settings.dashboard_session_ttl_seconds
    session_id = get_dashboard_session_store().create(
        password_verified=password_verified,
        totp_verified=totp_verified,
        ttl_seconds=ttl_seconds,
    )
    return session_id, ttl_seconds


def decorate_session_response(
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
    has_pwd = session_state is not None and session_state.password_verified
    totp_pending = (
        has_pwd and session_state is not None and response.totp_required_on_login and not session_state.totp_verified
    )
    fully_authorized = has_pwd and not totp_pending and response.password_required

    if request_auth is None:
        update: dict[str, object] = {
            "auth_mode": auth_mode,
            "password_management_enabled": password_management_enabled(auth_mode),
            "password_session_active": fully_authorized,
        }
        if (
            auth_mode == DashboardAuthMode.TRUSTED_HEADER
            and not response.password_required
            and not response.totp_required_on_login
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
        }
    )


async def has_active_password_session(request: Request, context: DashboardAuthContext) -> bool:
    settings = await context.repository.get_settings()
    if settings.password_hash is None:
        return False
    session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    return get_dashboard_session_store().is_password_verified(session_id)


async def validate_password_management_session(request: Request) -> None:
    ensure_password_management_enabled(request)

    session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    session_state = get_dashboard_session_store().get(session_id)
    if session_state is None or not session_state.password_verified:
        raise DashboardAuthError("Authentication is required")

    settings = await get_settings_cache().get()
    if settings.totp_required_on_login and not session_state.totp_verified:
        raise DashboardAuthError(
            "TOTP verification is required for dashboard access",
            code="totp_required",
        )


def ensure_password_management_enabled(request: Request) -> None:
    request_auth = get_dashboard_request_auth(request)
    if request_auth is not None and not password_management_enabled(request_auth.mode):
        raise DashboardBadRequestError(
            "Password and TOTP management is disabled while dashboard auth is bypassed",
            code="password_management_disabled",
        )


MAX_PASSWORD_BYTES = 72


def validate_password_length(password: str) -> None:
    if len(password) < 8:
        raise DashboardValidationError("Password must be at least 8 characters")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise DashboardValidationError(
            f"Password must be at most {MAX_PASSWORD_BYTES} bytes when encoded as UTF-8. "
            "Note that multi-byte characters (e.g. emoji, non-ASCII letters) count for more than one byte.",
            code="password_too_long",
        )


def set_session_cookie(response: JSONResponse, session_id: str, request: Request, *, max_age_seconds: int) -> None:
    response.set_cookie(
        key=DASHBOARD_SESSION_COOKIE,
        value=session_id,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=max_age_seconds,
        path="/",
    )
