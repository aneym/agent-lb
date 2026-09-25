from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, AccountResumeSchedule


class AccountScheduleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def account_exists(self, account_id: str) -> bool:
        return await self.session.scalar(select(Account.id).where(Account.id == account_id)) is not None

    async def set_resume_at(self, account_id: str, resume_at: datetime | None) -> None:
        if resume_at is None:
            await self.clear(account_id)
            return
        schedule = await self.session.get(AccountResumeSchedule, account_id)
        if schedule is None:
            self.session.add(AccountResumeSchedule(account_id=account_id, resume_at=resume_at))
        else:
            schedule.resume_at = resume_at
        await self.session.commit()

    async def list_schedules(self) -> list[AccountResumeSchedule]:
        rows = await self.session.scalars(select(AccountResumeSchedule).order_by(AccountResumeSchedule.resume_at))
        return list(rows.all())

    async def list_due(self, now: datetime) -> list[AccountResumeSchedule]:
        rows = await self.session.scalars(
            select(AccountResumeSchedule)
            .where(AccountResumeSchedule.resume_at <= now)
            .order_by(AccountResumeSchedule.resume_at)
        )
        return list(rows.all())

    async def clear(self, account_id: str, *, resume_at: datetime | None = None) -> None:
        stmt = delete(AccountResumeSchedule).where(AccountResumeSchedule.account_id == account_id)
        if resume_at is not None:
            stmt = stmt.where(AccountResumeSchedule.resume_at == resume_at)
        await self.session.execute(stmt)
        await self.session.commit()
