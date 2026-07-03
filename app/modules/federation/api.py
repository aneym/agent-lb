from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config.settings import get_settings
from app.dependencies import FederationContext, get_federation_context
from app.modules.federation.exceptions import (
    FederationConflictError,
    FederationNotConfiguredError,
    FederationNotFoundError,
)
from app.modules.federation.schemas import (
    FederationCheckinExecuteRequest,
    FederationCheckinExecuteResponse,
    FederationCheckinRequest,
    FederationCheckoutConfirmRequest,
    FederationCheckoutExecuteRequest,
    FederationCheckoutExecuteResponse,
    FederationCheckoutRequest,
    FederationCheckoutResponse,
    FederationMirrorResponse,
    FederationTransferStatusResponse,
)

_bearer_scheme = HTTPBearer(auto_error=False)


async def require_federation_peer_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    token = get_settings().federation_token
    if not token:
        raise HTTPException(status_code=403, detail="Federation is not enabled on this instance")
    if credentials is None or not hmac.compare_digest(credentials.credentials, token):
        raise HTTPException(status_code=403, detail="Invalid federation peer credentials")


router = APIRouter(
    prefix="/api/federation",
    tags=["federation"],
    dependencies=[Depends(require_federation_peer_auth)],
)


@router.get("/mirror", response_model=FederationMirrorResponse)
async def get_mirror(context: FederationContext = Depends(get_federation_context)) -> FederationMirrorResponse:
    return await context.service.build_mirror_response()


@router.post("/checkout", response_model=FederationCheckoutResponse)
async def post_checkout(
    request: FederationCheckoutRequest,
    context: FederationContext = Depends(get_federation_context),
) -> FederationCheckoutResponse:
    try:
        return await context.service.checkout(request.account_id, request.taker_instance_id)
    except FederationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FederationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/checkout/confirm", response_model=FederationTransferStatusResponse)
async def post_checkout_confirm(
    request: FederationCheckoutConfirmRequest,
    context: FederationContext = Depends(get_federation_context),
) -> FederationTransferStatusResponse:
    try:
        return await context.service.confirm_checkout(request.nonce)
    except FederationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/checkin", response_model=FederationTransferStatusResponse)
async def post_checkin(
    request: FederationCheckinRequest,
    context: FederationContext = Depends(get_federation_context),
) -> FederationTransferStatusResponse:
    try:
        return await context.service.checkin(request.account_id, request.nonce, request.auth)
    except FederationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/checkout/execute", response_model=FederationCheckoutExecuteResponse)
async def post_checkout_execute(
    request: FederationCheckoutExecuteRequest,
    context: FederationContext = Depends(get_federation_context),
) -> FederationCheckoutExecuteResponse:
    try:
        return await context.service.execute_checkout(request.account_id)
    except FederationNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/checkin/execute", response_model=FederationCheckinExecuteResponse)
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
