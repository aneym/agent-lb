from __future__ import annotations

import hashlib
import hmac

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.core.config.settings import get_settings
from app.dependencies import FederationContext, get_federation_context
from app.modules.federation.exceptions import (
    FederationConflictError,
    FederationNotConfiguredError,
    FederationNotFoundError,
    FederationPeerRequestError,
)
from app.modules.federation.push_auth import PushSource, require_federation_push_source
from app.modules.federation.push_receiver import FederationPushReceiver
from app.modules.federation.scheduler import FederationMirrorScheduler
from app.modules.federation.schemas import (
    FederationAbortRequest,
    FederationAbortResponse,
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
    FederationPushRequest,
    FederationPushResponse,
    FederationStatusResponse,
    FederationTransferStateResponse,
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
    if not token or credentials is None or not hmac.compare_digest(credentials.credentials, token):
        raise HTTPException(status_code=403, detail="Invalid federation mirror credentials")


async def require_federation_transfer_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    settings = get_settings()
    inbound = settings.federation_transfer_inbound_sha256
    if (
        not settings.federation_taker_instance_ids
        or not inbound
        or credentials is None
        or not hmac.compare_digest(hashlib.sha256(credentials.credentials.encode()).hexdigest(), inbound)
    ):
        raise HTTPException(status_code=403, detail="Invalid federation transfer credentials")


router = APIRouter(prefix="/api/federation", tags=["federation"])

dashboard_router = APIRouter(
    prefix="/api/federation",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.post("/push", response_model=FederationPushResponse)
async def post_push(
    request: FederationPushRequest,
    source: PushSource = Depends(require_federation_push_source),
    context: FederationContext = Depends(get_federation_context),
) -> FederationPushResponse:
    try:
        return await FederationPushReceiver(context.repository).receive(source, request)
    except FederationConflictError as exc:
        raise HTTPException(status_code=409, detail="Federation push owner conflict") from exc


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
    try:
        if not await context.service.reclaim(account_id):
            raise HTTPException(status_code=409, detail="Account cannot be reclaimed")
    except FederationNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"account_id": account_id, "owner_instance": get_settings().local_instance_id}


@router.post(
    "/checkout", dependencies=[Depends(require_federation_transfer_auth)], response_model=FederationCheckoutResponse
)
async def post_checkout(
    request: FederationCheckoutRequest,
    context: FederationContext = Depends(get_federation_context),
) -> FederationCheckoutResponse:
    if (
        request.taker_instance_id == get_settings().local_instance_id
        or request.taker_instance_id not in get_settings().federation_taker_instance_ids
    ):
        raise HTTPException(status_code=403, detail="Taker instance is not allowed")
    if not _valid_nonce(request.nonce):
        raise HTTPException(status_code=409, detail="Invalid checkout nonce")
    try:
        return await context.service.checkout(request.account_id, request.taker_instance_id, request.nonce)
    except FederationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FederationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _valid_nonce(nonce: str) -> bool:
    return 24 <= len(nonce) <= 128 and all(c.isalnum() and c.isascii() or c in "-_" for c in nonce)


@router.get(
    "/transfers/{nonce}",
    dependencies=[Depends(require_federation_transfer_auth)],
    response_model=FederationTransferStateResponse,
)
async def get_transfer_state(
    nonce: str, context: FederationContext = Depends(get_federation_context)
) -> FederationTransferStateResponse:
    return FederationTransferStateResponse(state=await context.service.transfer_status(nonce))


@router.post(
    "/transfers/{nonce}/abort",
    dependencies=[Depends(require_federation_transfer_auth)],
    response_model=FederationAbortResponse,
)
async def post_abort(
    nonce: str, body: FederationAbortRequest, context: FederationContext = Depends(get_federation_context)
) -> FederationAbortResponse:
    if not _valid_nonce(nonce) or body.caller_instance_id not in get_settings().federation_taker_instance_ids:
        raise HTTPException(status_code=403, detail="Caller is not allowed")
    try:
        return FederationAbortResponse(
            state=await context.service.abort(nonce, body.account_id, body.direction, body.caller_instance_id)
        )
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
    if (
        not _valid_nonce(request.nonce)
        or request.caller_instance_id not in get_settings().federation_taker_instance_ids
    ):
        raise HTTPException(status_code=403, detail="Caller is not allowed")
    try:
        return await context.service.checkin(
            request.account_id, request.nonce, request.caller_instance_id, request.auth
        )
    except FederationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FederationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@dashboard_router.post(
    "/checkout/execute",
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
    except FederationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@dashboard_router.post(
    "/checkin/execute",
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
    except FederationPeerRequestError as exc:
        raise HTTPException(
            status_code=409, detail=f"Checkin unresolved; reclaim {request.account_id} to retry"
        ) from exc
    except (OSError, TimeoutError, aiohttp.ClientError) as exc:
        raise HTTPException(
            status_code=503, detail=f"Checkin unresolved; reclaim {request.account_id} to retry"
        ) from exc
    except RuntimeError as exc:
        unreachable = isinstance(exc.__cause__, (OSError, TimeoutError, aiohttp.ClientError))
        raise HTTPException(status_code=503 if unreachable else 409, detail=str(exc)) from exc
