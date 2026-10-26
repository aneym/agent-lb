from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.core.utils.time import utcnow
from app.dependencies import AccountsContext, get_accounts_context
from app.modules.pools.pace_service import build_pace
from app.modules.t3_threads import service
from app.modules.t3_threads.repository import T3ThreadsRepository
from app.modules.t3_threads.schemas import BurnResponse, SyncRequest, SyncResponse

router = APIRouter(
    prefix="/api/t3/threads",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.post("/sync", response_model=SyncResponse)
async def sync_threads(
    payload: SyncRequest,
    context: AccountsContext = Depends(get_accounts_context),
) -> SyncResponse:
    moved = await T3ThreadsRepository(context.session).upsert(
        payload.host, payload.threads, payload.native_sessions, utcnow()
    )
    return SyncResponse(
        threads=len(payload.threads),
        native_sessions=len(payload.native_sessions),
        moved_sessions=moved,
    )


@router.get("/burn", response_model=BurnResponse)
async def thread_burn(
    range: Literal["24h", "7d"] = "24h",
    host: str | None = Query(default=None, pattern=r"^[a-z0-9-]{1,32}$"),
    pool: Literal["anthropic", "openai"] | None = None,
    root: str | None = None,
    context: AccountsContext = Depends(get_accounts_context),
) -> BurnResponse:
    summaries = await context.service.list_accounts()
    pace = await build_pace(summaries, context.session, utcnow())
    b_star = {name: pace["pools"].get(name, {}).get("b_star") for name in ("anthropic", "openai")}
    return await service.build_burn(context.session, summaries, b_star, window=range, host=host, pool=pool, root=root)
