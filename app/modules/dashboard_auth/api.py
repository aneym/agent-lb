from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

from app.core.auth.dashboard_mode import (
    DashboardAuthMode,
    get_dashboard_request_auth,
)
from app.core.auth.dependencies import set_dashboard_error_format
from app.core.bootstrap import (
    ensure_auto_bootstrap_token,
    get_bootstrap_validation_status,
    has_active_bootstrap_token,
    log_bootstrap_token,
)
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.exceptions import (
    DashboardAuthError,
    DashboardBadRequestError,
    DashboardConflictError,
    DashboardRateLimitError,
)
from app.core.request_locality import is_local_request
from app.dependencies import DashboardAuthContext, get_dashboard_auth_context
from app.modules.dashboard_auth.schemas import (
    DashboardAuthSessionResponse,
    PasswordChangeRequest,
    PasswordLoginRequest,
    PasswordRemoveRequest,
    PasswordSetupRequest,
    TotpSetupConfirmRequest,
    TotpSetupStartResponse,
    TotpVerifyRequest,
)
from app.modules.dashboard_auth.service import (
    DASHBOARD_SESSION_COOKIE,
    InvalidCredentialsError,
    PasswordAlreadyConfiguredError,
    PasswordNotConfiguredError,
    PasswordSessionRequiredError,
    TotpAlreadyConfiguredError,
    TotpInvalidCodeError,
    TotpInvalidSetupError,
    TotpNotConfiguredError,
    get_password_rate_limiter,
    get_totp_rate_limiter,
)
from app.modules.dashboard_auth.session_guards import (
    create_dashboard_session,
    decorate_session_response,
    ensure_password_management_enabled,
    has_active_password_session,
    session_client_key,
    set_session_cookie,
    validate_password_length,
    validate_password_management_session,
)

router = APIRouter(
    prefix="/api/dashboard-auth",
    tags=["dashboard"],
    dependencies=[Depends(set_dashboard_error_format)],
)

logger = logging.getLogger(__name__)


@router.get("/session", response_model=DashboardAuthSessionResponse)
async def get_dashboard_auth_session(
    request: Request,
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> DashboardAuthSessionResponse:
    session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    response = await context.service.get_session_state(session_id)
    decorated = decorate_session_response(response, request=request, force_authenticated=True)
    if decorated.auth_mode != DashboardAuthMode.STANDARD:
        return decorated
    if decorated.password_required or is_local_request(request):
        return decorated
    bootstrap_token_configured = await has_active_bootstrap_token()
    return decorated.model_copy(
        update={
            "authenticated": False,
            "bootstrap_required": True,
            "bootstrap_token_configured": bootstrap_token_configured,
        }
    )


@router.post("/password/setup", response_model=DashboardAuthSessionResponse)
async def setup_password(
    request: Request,
    payload: PasswordSetupRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> DashboardAuthSessionResponse | JSONResponse:
    settings = get_settings()
    request_auth = get_dashboard_request_auth(request)
    current_settings = await context.repository.get_settings()
    if settings.dashboard_auth_mode == DashboardAuthMode.DISABLED:
        raise DashboardBadRequestError(
            "Password management is disabled while dashboard auth is bypassed",
            code="password_management_disabled",
        )
    if (
        settings.dashboard_auth_mode == DashboardAuthMode.TRUSTED_HEADER
        and request_auth is None
        and current_settings.password_hash is None
    ):
        raise DashboardAuthError("Reverse proxy authentication is required", code="proxy_auth_required")
    if (
        current_settings.password_hash is None
        and settings.dashboard_auth_mode != DashboardAuthMode.TRUSTED_HEADER
        and not is_local_request(request)
    ):
        submitted_bootstrap_token = (payload.bootstrap_token or "").strip()
        validation_status = await get_bootstrap_validation_status(submitted_bootstrap_token)
        if validation_status == "unavailable":
            raise DashboardAuthError(
                "Remote bootstrap is disabled until AGENT_LB_DASHBOARD_BOOTSTRAP_TOKEN is configured.",
                code="bootstrap_unavailable",
            )
        if validation_status == "password_already_configured":
            raise DashboardConflictError("Password is already configured", code="password_already_configured")
        if validation_status != "valid":
            raise DashboardAuthError("Invalid dashboard bootstrap token.", code="invalid_bootstrap_token")
    password = payload.password.strip()
    validate_password_length(password)
    try:
        await context.service.setup_password(password)
    except PasswordAlreadyConfiguredError as exc:
        raise DashboardConflictError(str(exc), code="password_already_configured") from exc

    await get_settings_cache().invalidate()
    session_id, session_ttl_seconds = await create_dashboard_session(password_verified=True, totp_verified=False)
    response = decorate_session_response(
        await context.service.get_session_state(session_id),
        request=request,
        password_session_id=session_id,
    )
    json_response = JSONResponse(status_code=200, content=response.model_dump(by_alias=True))
    set_session_cookie(json_response, session_id, request, max_age_seconds=session_ttl_seconds)
    return json_response


@router.post("/password/login", response_model=DashboardAuthSessionResponse)
async def login_password(
    request: Request,
    payload: PasswordLoginRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> DashboardAuthSessionResponse | JSONResponse:
    if get_settings().dashboard_auth_mode == DashboardAuthMode.DISABLED:
        raise DashboardBadRequestError(
            "Password login is disabled while dashboard auth is bypassed",
            code="password_management_disabled",
        )

    settings = await get_settings_cache().get()
    if settings.password_hash is None:
        raise DashboardBadRequestError("Password is not configured", code="password_not_configured")

    limiter = get_password_rate_limiter()
    rate_key = session_client_key(request, prefix="password_login")
    try:
        await limiter.check_and_increment(rate_key, context.session)
    except DashboardRateLimitError as exc:
        raise DashboardRateLimitError(
            f"Too many attempts. Try again in {exc.retry_after} seconds.",
            retry_after=exc.retry_after,
            code="password_rate_limited",
        ) from exc

    try:
        await context.service.verify_password(
            payload.password, actor_ip=request.client.host if request.client else None
        )
    except InvalidCredentialsError as exc:
        raise DashboardAuthError(str(exc), code="invalid_credentials") from exc
    except PasswordNotConfiguredError as exc:
        await limiter.clear_for_key(rate_key, context.session)
        raise DashboardBadRequestError(str(exc), code="password_not_configured") from exc

    await limiter.clear_for_key(rate_key, context.session)

    session_id, session_ttl_seconds = await create_dashboard_session(password_verified=True, totp_verified=False)
    response = decorate_session_response(
        await context.service.get_session_state(session_id),
        request=request,
        password_session_id=session_id,
    )
    json_response = JSONResponse(status_code=200, content=response.model_dump(by_alias=True))
    set_session_cookie(json_response, session_id, request, max_age_seconds=session_ttl_seconds)
    return json_response


@router.post("/password/change")
async def change_password(
    request: Request,
    payload: PasswordChangeRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> JSONResponse:
    await validate_password_management_session(request)

    new_password = payload.new_password.strip()
    validate_password_length(new_password)

    try:
        await context.service.change_password(payload.current_password, new_password)
    except PasswordNotConfiguredError as exc:
        raise DashboardBadRequestError(str(exc), code="password_not_configured") from exc
    except InvalidCredentialsError as exc:
        raise DashboardAuthError(str(exc), code="invalid_credentials") from exc

    await get_settings_cache().invalidate()
    return JSONResponse(status_code=200, content={"status": "ok"})


@router.delete("/password")
async def remove_password(
    request: Request,
    payload: PasswordRemoveRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> JSONResponse:
    await validate_password_management_session(request)

    try:
        await context.service.remove_password(payload.password)
    except PasswordNotConfiguredError as exc:
        raise DashboardBadRequestError(str(exc), code="password_not_configured") from exc
    except InvalidCredentialsError as exc:
        raise DashboardAuthError(str(exc), code="invalid_credentials") from exc

    await get_settings_cache().invalidate()
    bootstrap_token = await ensure_auto_bootstrap_token()
    if bootstrap_token:
        log_bootstrap_token(logger, bootstrap_token, reason="password-removed")
    response = JSONResponse(status_code=200, content={"status": "ok"})
    response.delete_cookie(key=DASHBOARD_SESSION_COOKIE, path="/")
    return response


@router.post("/totp/setup/start", response_model=TotpSetupStartResponse)
async def start_totp_setup(
    request: Request,
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
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
        session_ttl_seconds = (await get_settings_cache().get()).dashboard_session_ttl_seconds
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
        await context.service.get_session_state(session_id),
        request=request,
        password_session_id=session_id,
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
            session_id=session_id,
            code=payload.code,
            actor_ip=request.client.host if request.client else None,
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


@router.post("/logout")
async def logout_dashboard(
    request: Request,
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> JSONResponse:
    session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    context.service.logout(session_id)
    response = JSONResponse(status_code=200, content={"status": "ok"})
    response.delete_cookie(key=DASHBOARD_SESSION_COOKIE, path="/")
    return response
