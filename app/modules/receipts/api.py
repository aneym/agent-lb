from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.db.session import get_session
from app.modules.receipts.repository import ReceiptFilters, ReceiptsRepository
from app.modules.receipts.schemas import ReceiptsResponse, ReceiptSummary
from app.modules.receipts.service import ReceiptsService

router = APIRouter(
    prefix="/api/receipts",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


def _filters(since, until, account_id, api_key_id, session_id, provider, pool, model, status):
    return ReceiptFilters(since, until, account_id, api_key_id, session_id, provider, pool, model, status)


@router.get("", response_model=ReceiptsResponse)
async def list_receipts(
    since: datetime | None = None,
    until: datetime | None = None,
    account_id: list[str] | None = Query(default=None),
    api_key_id: list[str] | None = Query(default=None),
    session_id: str | None = None,
    provider: str | None = None,
    pool: str | None = None,
    model: list[str] | None = Query(default=None),
    status: Literal["ok", "error"] | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> ReceiptsResponse:
    return await ReceiptsService(ReceiptsRepository(session)).page(
        _filters(since, until, account_id, api_key_id, session_id, provider, pool, model, status),
        limit,
        offset,
    )


@router.get("/summary", response_model=ReceiptSummary)
async def summary(
    since: datetime | None = None,
    until: datetime | None = None,
    account_id: list[str] | None = Query(default=None),
    api_key_id: list[str] | None = Query(default=None),
    session_id: str | None = None,
    provider: str | None = None,
    pool: str | None = None,
    model: list[str] | None = Query(default=None),
    status: Literal["ok", "error"] | None = None,
    group_by: Literal["account", "pool", "key", "session", "model", "day", "provider"] = "account",
    bucket: Literal["hour", "6h", "day"] = "6h",
    session: AsyncSession = Depends(get_session),
) -> ReceiptSummary:
    return await ReceiptsService(ReceiptsRepository(session)).summary(
        _filters(since, until, account_id, api_key_id, session_id, provider, pool, model, status),
        group_by,
        bucket,
    )
