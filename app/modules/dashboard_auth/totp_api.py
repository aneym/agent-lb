from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

from app.core.auth.dashboard_session_ttl import resolve_dashboard_session_ttl_seconds
from app.core.config.settings_cache import get_settings_cache
from app.core.exceptions import DashboardAuthError, DashboardBadRequestError, DashboardRateLimitError
from app.dependencies import DashboardAuthContext, get_dashboard_auth_context
from app.modules.dashboard_auth.schemas import (
    DashboardAuthSessionResponse,
    TotpSetupConfirmRequest,
    TotpSetupStartResponse,
    TotpVerifyRequest,
)
from app.modules.dashboard_auth.service import (
    DASHBOARD_SESSION_COOKIE,
    PasswordSessionRequiredError,
    TotpAlreadyConfiguredError,
    TotpInvalidCodeError,
    TotpInvalidSetupError,
    TotpNotConfiguredError,
    get_totp_rate_limiter,
)
from app.modules.dashboard_auth.session_guards import (
    decorate_session_response,
    ensure_password_management_enabled,
    has_active_password_session,
    session_client_key,
    set_session_cookie,
)

router = APIRouter()


@router.post("/totp/setup/start", response_model=TotpSetupStartResponse)
async def start_totp_setup(
    request: Request, context: DashboardAuthContext = Depends(get_dashboard_auth_context)
) -> TotpSetupStartResponse:
    ensure_password_management_enabled(request)
    if not await has_active_password_session(request, context):
        raise DashboardAuthError("Authentication is required")
    session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    try:
        return await context.service.start_totp_setup(session_id=session_id)
    except PasswordSessionRequiredError as exc:
        raise DashboardAuthError(str(exc)) from exc
    except TotpAlreadyConfiguredError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_setup") from exc


@router.post("/totp/setup/confirm")
async def confirm_totp_setup(
    request: Request,
    payload: TotpSetupConfirmRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> JSONResponse:
    ensure_password_management_enabled(request)
    if not await has_active_password_session(request, context):
        raise DashboardAuthError("Authentication is required")
    limiter = get_totp_rate_limiter()
    rate_key = session_client_key(request, prefix="totp_setup_confirm")
    try:
        await limiter.check_and_increment(rate_key, context.session)
    except DashboardRateLimitError as exc:
        raise DashboardRateLimitError(
            f"Too many attempts. Try again in {exc.retry_after} seconds.",
            retry_after=exc.retry_after,
            code="totp_rate_limited",
        ) from exc
    try:
        session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
        await context.service.confirm_totp_setup(
            session_id=session_id,
            secret=payload.secret,
            code=payload.code,
            actor_ip=request.client.host if request.client else None,
        )
    except PasswordSessionRequiredError as exc:
        raise DashboardAuthError(str(exc)) from exc
    except TotpInvalidCodeError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_code") from exc
    except TotpInvalidSetupError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_setup") from exc
    except TotpAlreadyConfiguredError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_setup") from exc
    await limiter.clear_for_key(rate_key, context.session)
    await get_settings_cache().invalidate()
    return JSONResponse(status_code=200, content={"status": "ok"})


@router.post("/totp/verify", response_model=DashboardAuthSessionResponse)
async def verify_totp(
    request: Request,
    payload: TotpVerifyRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> DashboardAuthSessionResponse | JSONResponse:
    ensure_password_management_enabled(request)
    limiter = get_totp_rate_limiter()
    rate_key = session_client_key(request, prefix="totp_verify")
    current_session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    try:
        await context.service.ensure_active_password_session(current_session_id)
    except PasswordSessionRequiredError as exc:
        raise DashboardAuthError(str(exc)) from exc
    try:
        await limiter.check_and_increment(rate_key, context.session)
    except DashboardRateLimitError as exc:
        raise DashboardRateLimitError(
            f"Too many attempts. Try again in {exc.retry_after} seconds.",
            retry_after=exc.retry_after,
            code="totp_rate_limited",
        ) from exc
    try:
        configured_ttl_seconds = (await get_settings_cache().get()).dashboard_session_ttl_seconds
        session_ttl_seconds = resolve_dashboard_session_ttl_seconds(request, configured_ttl_seconds)
        session_id, applied_ttl_seconds = await context.service.verify_totp(
            session_id=current_session_id,
            code=payload.code,
            ttl_seconds=session_ttl_seconds,
            actor_ip=request.client.host if request.client else None,
        )
    except PasswordSessionRequiredError as exc:
        raise DashboardAuthError(str(exc)) from exc
    except TotpInvalidCodeError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_code") from exc
    except TotpNotConfiguredError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_code") from exc
    await limiter.clear_for_key(rate_key, context.session)
    response = decorate_session_response(
        await context.service.get_session_state(session_id), request=request, password_session_id=session_id
    )
    json_response = JSONResponse(status_code=200, content=response.model_dump(by_alias=True))
    set_session_cookie(json_response, session_id, request, max_age_seconds=applied_ttl_seconds)
    return json_response


@router.post("/totp/disable")
async def disable_totp(
    request: Request,
    payload: TotpVerifyRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> JSONResponse:
    ensure_password_management_enabled(request)
    limiter = get_totp_rate_limiter()
    rate_key = session_client_key(request, prefix="totp_disable")
    session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    try:
        await context.service.ensure_totp_verified_session(session_id)
    except PasswordSessionRequiredError as exc:
        raise DashboardAuthError(str(exc)) from exc
    try:
        await limiter.check_and_increment(rate_key, context.session)
    except DashboardRateLimitError as exc:
        raise DashboardRateLimitError(
            f"Too many attempts. Try again in {exc.retry_after} seconds.",
            retry_after=exc.retry_after,
            code="totp_rate_limited",
        ) from exc
    try:
        await context.service.disable_totp(
            session_id=session_id, code=payload.code, actor_ip=request.client.host if request.client else None
        )
    except PasswordSessionRequiredError as exc:
        raise DashboardAuthError(str(exc)) from exc
    except TotpInvalidCodeError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_code") from exc
    except TotpNotConfiguredError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_code") from exc
    await limiter.clear_for_key(rate_key, context.session)
    await get_settings_cache().invalidate()
    return JSONResponse(status_code=200, content={"status": "ok"})
