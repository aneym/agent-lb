from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.core.config.settings import get_settings
from app.dependencies import FederationContext, get_federation_context
from app.modules.federation.exceptions import (
    FederationConflictError,
    FederationNotConfiguredError,
    FederationNotFoundError,
)
from app.modules.federation.scheduler import FederationMirrorScheduler
from app.modules.federation.schemas import (
    FederationAccountCounts,
    FederationCheckinExecuteRequest,
    FederationCheckinExecuteResponse,
    FederationCheckinRequest,
    FederationCheckoutConfirmRequest,
    FederationCheckoutExecuteRequest,
    FederationCheckoutExecuteResponse,
    FederationCheckoutRequest,
    FederationCheckoutResponse,
    FederationMirrorResponse,
    FederationMirrorStatus,
    FederationStatusResponse,
    FederationTransferStatusResponse,
    FederationUsagePushStatus,
    FederationUsageReportRequest,
    FederationUsageReportResponse,
)

_bearer_scheme = HTTPBearer(auto_error=False)


async def require_federation_mirror_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    settings = get_settings()
    token = settings.effective_federation_mirror_token
    if (
        not token
        or (settings.federation_transfer_token and hmac.compare_digest(token, settings.federation_transfer_token))
        or credentials is None
        or not hmac.compare_digest(credentials.credentials, token)
    ):
        raise HTTPException(status_code=403, detail="Invalid federation mirror credentials")


async def require_federation_transfer_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    settings = get_settings()
    # The taker has an outbound credential but no inbound transfer authority.
    token = settings.federation_transfer_token if settings.federation_taker_instance_ids else None
    mirror_token = settings.effective_federation_mirror_token
    if (
        not token
        or (settings.federation_token and hmac.compare_digest(token, settings.federation_token))
        or (mirror_token and hmac.compare_digest(token, mirror_token))
        or credentials is None
        or not hmac.compare_digest(credentials.credentials, token)
    ):
        raise HTTPException(status_code=403, detail="Invalid federation transfer credentials")


router = APIRouter(prefix="/api/federation", tags=["federation"])

dashboard_router = APIRouter(
    prefix="/api/federation",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.get("/mirror", response_model=FederationMirrorResponse, dependencies=[Depends(require_federation_mirror_auth)])
async def get_mirror(context: FederationContext = Depends(get_federation_context)) -> FederationMirrorResponse:
    return await context.service.build_mirror_response()


@router.post(
    "/usage-report",
    response_model=FederationUsageReportResponse,
    dependencies=[Depends(require_federation_mirror_auth)],
)
async def post_usage_report(
    request: FederationUsageReportRequest,
    context: FederationContext = Depends(get_federation_context),
) -> FederationUsageReportResponse:
    return await context.service.accept_usage_report(request.instance_id, request.rollups)


@dashboard_router.get("/status", response_model=FederationStatusResponse)
async def get_status(
    request: Request,
    context: FederationContext = Depends(get_federation_context),
) -> FederationStatusResponse:
    settings = get_settings()
    scheduler: FederationMirrorScheduler | None = getattr(request.app.state, "federation_mirror_scheduler", None)
    owned, mirrored = await context.repository.count_accounts_by_ownership(settings.local_instance_id)
    is_enabled = bool(settings.federation_peer_url and settings.effective_federation_mirror_token)
    return FederationStatusResponse(
        local_instance_id=settings.local_instance_id,
        token_configured=bool(settings.effective_federation_mirror_token),
        peer_url=settings.federation_peer_url,
        mirror=FederationMirrorStatus(
            enabled=is_enabled,
            interval_seconds=settings.federation_mirror_interval_seconds,
            last_success_at=scheduler.last_success_at if scheduler else None,
            last_attempt_at=scheduler.last_attempt_at if scheduler else None,
            consecutive_failures=scheduler.consecutive_failures if scheduler else 0,
            last_error=scheduler.last_error if scheduler else None,
        ),
        usage_push=FederationUsagePushStatus(
            last_success_at=scheduler.usage_push_last_success_at if scheduler else None,
            last_error=scheduler.usage_push_last_error if scheduler else None,
        ),
        accounts=FederationAccountCounts(owned=owned, mirrored=mirrored),
    )


@dashboard_router.post("/reclaim/{account_id}")
async def post_reclaim(
    account_id: str,
    context: FederationContext = Depends(get_federation_context),
) -> dict[str, str]:
    if not await context.service.reclaim(account_id):
        raise HTTPException(status_code=409, detail="Account cannot be reclaimed")
    return {"account_id": account_id, "owner_instance": get_settings().local_instance_id}


@router.post(
    "/checkout", dependencies=[Depends(require_federation_transfer_auth)], response_model=FederationCheckoutResponse
)
async def post_checkout(
    request: FederationCheckoutRequest,
    context: FederationContext = Depends(get_federation_context),
) -> FederationCheckoutResponse:
    if request.taker_instance_id not in get_settings().federation_taker_instance_ids:
        raise HTTPException(status_code=403, detail="Taker instance is not allowed")
    try:
        return await context.service.checkout(request.account_id, request.taker_instance_id)
    except FederationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FederationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/checkout/confirm",
    dependencies=[Depends(require_federation_transfer_auth)],
    response_model=FederationTransferStatusResponse,
)
async def post_checkout_confirm(
    request: FederationCheckoutConfirmRequest,
    context: FederationContext = Depends(get_federation_context),
) -> FederationTransferStatusResponse:
    try:
        return await context.service.confirm_checkout(request.nonce)
    except FederationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/checkin",
    dependencies=[Depends(require_federation_transfer_auth)],
    response_model=FederationTransferStatusResponse,
)
async def post_checkin(
    request: FederationCheckinRequest,
    context: FederationContext = Depends(get_federation_context),
) -> FederationTransferStatusResponse:
    try:
        return await context.service.checkin(request.account_id, request.nonce, request.auth)
    except FederationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FederationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/checkout/execute",
    dependencies=[Depends(require_federation_transfer_auth)],
    response_model=FederationCheckoutExecuteResponse,
)
async def post_checkout_execute(
    request: FederationCheckoutExecuteRequest,
    context: FederationContext = Depends(get_federation_context),
) -> FederationCheckoutExecuteResponse:
    try:
        return await context.service.execute_checkout(request.account_id)
    except FederationNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/checkin/execute",
    dependencies=[Depends(require_federation_transfer_auth)],
    response_model=FederationCheckinExecuteResponse,
)
async def post_checkin_execute(
    request: FederationCheckinExecuteRequest,
    context: FederationContext = Depends(get_federation_context),
) -> FederationCheckinExecuteResponse:
    try:
        return await context.service.execute_checkin(request.account_id)
    except FederationNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except FederationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FederationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
