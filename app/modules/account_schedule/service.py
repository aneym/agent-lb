from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.time import to_utc_naive
from app.modules.account_schedule.repository import AccountScheduleRepository
from app.modules.account_schedule.schemas import ResumeSchedule, ResumeSchedulesResponse


class AccountScheduleService:
    def __init__(self, session: AsyncSession) -> None:
        self.repository = AccountScheduleRepository(session)

    async def set_resume_at(self, account_id: str, resume_at: datetime | None) -> ResumeSchedule | None:
        if not await self.repository.account_exists(account_id):
            return None
        normalized = to_utc_naive(resume_at) if resume_at is not None else None
        await self.repository.set_resume_at(account_id, normalized)
        return ResumeSchedule(account_id=account_id, resume_at=normalized)

    async def list_schedules(self) -> ResumeSchedulesResponse:
        rows = await self.repository.list_schedules()
        return ResumeSchedulesResponse(
            schedules=[ResumeSchedule(account_id=row.account_id, resume_at=row.resume_at) for row in rows]
        )
