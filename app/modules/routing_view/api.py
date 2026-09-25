from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.modules.routing_view.decisions import read_decisions
from app.modules.routing_view.menu import get_menu
from app.modules.routing_view.schemas import DecisionsResponse

router = APIRouter(
    tags=["dashboard"], dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)]
)


@router.get("/api/menu")
async def menu(task_class: str | None = Query(default=None, alias="class")) -> Any:
    return await get_menu(task_class)


@router.get("/api/route-decisions", response_model=DecisionsResponse)
async def route_decisions(
    since: datetime | None = None,
    until: datetime | None = None,
    task_class: str | None = Query(default=None, alias="class"),
    outcome: Literal["ok", "failed", "no_pick", "open"] | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> DecisionsResponse:
    return read_decisions(since=since, until=until, task_class=task_class, outcome=outcome, limit=limit)
