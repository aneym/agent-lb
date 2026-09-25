from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


class ResumeAtRequest(DashboardModel):
    resume_at: datetime | None


class ResumeSchedule(DashboardModel):
    account_id: str
    resume_at: datetime | None


class ResumeSchedulesResponse(DashboardModel):
    schedules: list[ResumeSchedule] = Field(default_factory=list)
