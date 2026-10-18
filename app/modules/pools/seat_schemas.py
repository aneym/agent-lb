from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


class SeatAccountUsage(DashboardModel):
    runs: int = 0
    ok: int = 0
    wall_s: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0


class SeatModelUsage(DashboardModel):
    runs: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    ok: int | None = Field(default=None, exclude=True)
    tokens_known: bool = Field(default=False, exclude=True)


class SeatQuotaWindow(DashboardModel):
    id: str
    kind: str
    label: str
    duration_mins: int | None = None
    used_percent: float | None = None
    remaining_percent: float | None = None
    resets_at: datetime | None = None
    source: str


class SeatQuota(DashboardModel):
    source: str = "unavailable"
    observed_at: datetime | None = None
    stale: bool = False
    unavailable_reason: str | None = "not_collected"
    primary_window_id: str | None = None
    windows: list[SeatQuotaWindow] = Field(default_factory=list)


class SeatTodayModel(DashboardModel):
    model: str
    runs: int = 0
    tokens_in: int | None = None
    tokens_out: int | None = None


class SeatToday(DashboardModel):
    runs: int = 0
    ok: int = 0
    ok_known: bool = True
    tokens_in: int | None = None
    tokens_out: int | None = None
    tokens_known: bool = False
    value_usd: float | None = None
    value_source: str | None = None
    models: list[SeatTodayModel] = Field(default_factory=list)


class SeatRun(DashboardModel):
    ok: bool | None = None
    wall_s: float | None = None
    ts: datetime
    model: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cache_read: int | None = None


class SeatAccount(DashboardModel):
    email: str | None = None
    plan_label: str | None = None
    status: str = "active"
    status_reset_at: datetime | None = None
    quota: SeatQuota = Field(default_factory=SeatQuota)
    today: SeatToday = Field(default_factory=SeatToday)
    id: str
    vendor: str
    auth: str | None = None
    enabled: bool = True
    auth_ok: bool | None = None
    auth_checked_at: datetime | None = None
    tier: str | None = None
    plan: str | None = None
    cycle_day: int | None = None
    daily: dict[str, dict[str, SeatModelUsage]] = Field(default_factory=dict)
    recent_runs: list[SeatRun] = Field(default_factory=list)
    cooldown_until: datetime | None = None
    cooldowns: dict[str, datetime] = Field(default_factory=dict)
    limits: dict[str, dict[str, Any]] = Field(default_factory=dict, exclude=True)
    last_error_kind: str | None = None
    last_error_at: datetime | None = None
    last_run_at: datetime | None = None
    ready: bool = False
    last_day: SeatAccountUsage = Field(default_factory=SeatAccountUsage)


class SeatAccountsResponse(DashboardModel):
    quota_refreshed_at: datetime | None = None
    state_updated_at: datetime | None = None
    source: str
    accounts: list[SeatAccount] = Field(default_factory=list)
