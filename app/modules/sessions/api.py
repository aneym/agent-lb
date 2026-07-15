from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.dependencies import SessionsContext, get_sessions_context
from app.modules.sessions.schemas import SessionDetailResponse, SessionListResponse

router = APIRouter(
    prefix="/api/sessions",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)
short_link_router = APIRouter(
    tags=["dashboard"],
    dependencies=[Depends(set_dashboard_error_format)],
)


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    window_minutes: int = Query(4320, ge=1, alias="windowMinutes"),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    context: SessionsContext = Depends(get_sessions_context),
) -> SessionListResponse:
    page = await context.service.list_sessions(
        window_minutes=window_minutes,
        limit=limit,
        offset=offset,
    )
    return SessionListResponse(sessions=page.sessions, total=page.total)


@router.get("/{session_id}", response_model=SessionDetailResponse)
async def get_session(
    session_id: str,
    context: SessionsContext = Depends(get_sessions_context),
) -> SessionDetailResponse:
    detail = await context.service.get_session(session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return detail


@short_link_router.get("/s/{session_id_or_prefix}", include_in_schema=False)
async def redirect_session_short_link(
    session_id_or_prefix: str,
    context: SessionsContext = Depends(get_sessions_context),
) -> RedirectResponse:
    value = session_id_or_prefix.strip()
    if len(value) < 8:
        raise HTTPException(status_code=404, detail="Session not found")
    matches = await context.service.resolve_session_ids(value)
    if not matches:
        raise HTTPException(status_code=404, detail="Session not found")
    if len(matches) > 1:
        raise HTTPException(status_code=409, detail="Session prefix is ambiguous")
    return RedirectResponse(url=f"/sessions?session={matches[0]}", status_code=302)
