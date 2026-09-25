from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.core.exceptions import DashboardNotFoundError
from app.db.session import get_session
from app.modules.account_schedule.schemas import ResumeAtRequest, ResumeSchedule, ResumeSchedulesResponse
from app.modules.account_schedule.service import AccountScheduleService

router = APIRouter(
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.put("/api/accounts/{account_id}/resume-at", response_model=ResumeSchedule)
async def set_resume_at(
    account_id: str,
    payload: ResumeAtRequest,
    session: AsyncSession = Depends(get_session),
) -> ResumeSchedule:
    result = await AccountScheduleService(session).set_resume_at(account_id, payload.resume_at)
    if result is None:
        raise DashboardNotFoundError("Account not found", code="account_not_found")
    return result


@router.get("/api/account-resume-schedules", response_model=ResumeSchedulesResponse)
async def list_schedules(session: AsyncSession = Depends(get_session)) -> ResumeSchedulesResponse:
    return await AccountScheduleService(session).list_schedules()
