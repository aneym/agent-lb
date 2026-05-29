from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.settings import get_settings
from app.db.session import get_session
from app.modules.public_usage.schemas import PublicUsageResponse
from app.modules.public_usage.service import build_public_usage

# No dashboard-session dependency: this surface is intentionally anonymous and public.
# It returns only aggregate numbers (never account_id / email / api_key / bodies / IPs).
router = APIRouter(prefix="/api/usage", tags=["public"])


@router.get("/public", response_model=PublicUsageResponse)
async def get_public_usage(
    response: Response,
    days: int = Query(default=365),
    session: AsyncSession = Depends(get_session),
) -> PublicUsageResponse:
    if not get_settings().public_usage_enabled:
        raise HTTPException(status_code=404, detail="Not found")
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Cache-Control"] = "public, max-age=300"
    return await build_public_usage(session, days)
