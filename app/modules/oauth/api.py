from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.core.clients.oauth import OAuthError
from app.core.config.settings import get_settings
from app.core.errors import dashboard_error
from app.core.exceptions import DashboardConflictError
from app.dependencies import OauthContext, get_oauth_context
from app.modules.oauth.schemas import (
    ManualCallbackRequest,
    ManualCallbackResponse,
    OauthCompleteRequest,
    OauthCompleteResponse,
    OauthStartRequest,
    OauthStartResponse,
    OauthStatusResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/oauth",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


def _require_oauth_owner() -> None:
    """Block OAuth on a federation follower.

    A follower mirrors the owner's accounts but never receives refresh
    tokens for them (app/modules/federation/schemas.py), so completing OAuth
    here would persist a local-only credential the owner can never pick up.
    Fail visibly and point at the owner instead of silently accepting it.
    """
    peer_url = get_settings().federation_peer_url
    if peer_url:
        raise DashboardConflictError(
            "This instance mirrors accounts from the federation owner and cannot "
            f"complete OAuth locally. Run OAuth against the owner instance instead: {peer_url}",
            code="oauth_owner_required",
        )


@router.post("/start", response_model=OauthStartResponse)
async def start_oauth(
    request: OauthStartRequest,
    context: OauthContext = Depends(get_oauth_context),
) -> OauthStartResponse | JSONResponse:
    _require_oauth_owner()
    try:
        return await context.service.start_oauth(request)
    except OAuthError as exc:
        return JSONResponse(
            status_code=502,
            content=dashboard_error(exc.code, exc.message),
        )
    except NotImplementedError:
        return JSONResponse(
            status_code=501,
            content=dashboard_error("not_implemented", "OAuth start is not implemented"),
        )


@router.get("/status", response_model=OauthStatusResponse)
async def oauth_status(
    flow_id: str | None = Query(default=None, alias="flowId"),
    context: OauthContext = Depends(get_oauth_context),
) -> OauthStatusResponse | JSONResponse:
    return await context.service.oauth_status(flow_id=flow_id)


@router.post("/complete", response_model=OauthCompleteResponse)
async def complete_oauth(
    request: OauthCompleteRequest | None = Body(default=None),
    context: OauthContext = Depends(get_oauth_context),
) -> OauthCompleteResponse | JSONResponse:
    _require_oauth_owner()
    try:
        return await context.service.complete_oauth(request)
    except NotImplementedError:
        return JSONResponse(
            status_code=501,
            content=dashboard_error("not_implemented", "OAuth complete is not implemented"),
        )


@router.post("/manual-callback", response_model=ManualCallbackResponse)
async def manual_callback(
    request: ManualCallbackRequest,
    context: OauthContext = Depends(get_oauth_context),
) -> ManualCallbackResponse | JSONResponse:
    _require_oauth_owner()
    try:
        return await context.service.manual_callback(request.callback_url, flow_id=request.flow_id)
    except OAuthError as exc:
        return JSONResponse(
            status_code=502,
            content=dashboard_error(exc.code, exc.message),
        )
    except Exception:
        logger.exception("manual_callback failed")
        return JSONResponse(
            status_code=500,
            content=dashboard_error("manual_callback_failed", "An internal error occurred."),
        )
