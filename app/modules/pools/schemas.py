from __future__ import annotations

from datetime import datetime

from pydantic import Field, computed_field

from app.modules.shared.schemas import DashboardModel

POOL_STATUS_OK = "ok"
POOL_STATUS_LOW = "low"
POOL_STATUS_EXHAUSTED = "exhausted"

POOL_SOURCE_SCOPED_MARKER = "scoped_marker"
POOL_SOURCE_WEEKLY_HEURISTIC = "weekly_heuristic"


class PoolRefill(DashboardModel):
    at: datetime
    accounts: int
    remaining_percent: float


class PoolWindow(DashboardModel):
    id: str
    kind: str
    label: str
    remaining_percent: float | None = None
    used_percent: float | None = None
    resets_at: datetime | None = None
    measured_accounts: int = 0
    source: str


class PoolSummary(DashboardModel):
    windows: list[PoolWindow] | None = Field(default=None, exclude_if=lambda value: value is None)
    quota_source: str | None = Field(default=None, exclude_if=lambda value: value is None)

    id: str
    provider: str
    kind: str

    accounts: int
    eligible_accounts: int
    weekly_empty_accounts: int = Field(default=0, exclude=True)
    refills: list[PoolRefill] | None = Field(default=None, exclude_if=lambda value: value is None)
    plan_count: int | None = Field(default=None, exclude=True)

    @computed_field
    @property
    def total_accounts(self) -> int:
        return self.accounts

    @computed_field
    @property
    def usable_accounts(self) -> int:
        return self.eligible_accounts

    headroom_percent: float | None = None
    aggregate_remaining_percent: float | None = None

    reset_at: datetime | None = None
    five_hour_remaining_percent: float | None = None
    five_hour_reset_at: datetime | None = None
    window_label: str | None = Field(default=None, pattern=r"^(week|month)$")
    status: str = Field(pattern=r"^(ok|low|exhausted)$")
    unavailable_reason: str | None = Field(default=None, exclude_if=lambda value: value is None)

    source: str | None = None

    weekly_remaining_percent: float | None = None
    weekly_reset_at: datetime | None = None

    weekly_pace_percent: float | None = None

    observed_runs: int | None = None
    observed_tokens: int | None = None
    spent_usd: float | None = Field(default=None, exclude_if=lambda value: value is None)
    budget_usd: float | None = Field(default=None, exclude_if=lambda value: value is None)
    monthly_remaining_percent: float | None = Field(default=None, exclude_if=lambda value: value is None)
    monthly_pace_percent: float | None = Field(default=None, exclude_if=lambda value: value is None)
    burn24h_percent: float | None = Field(default=None, alias="burn24hPercent", exclude_if=lambda value: value is None)
    percent_used: float | None = Field(default=None, exclude_if=lambda value: value is None)
    percent_source: str | None = Field(
        default=None, pattern=r"^(vendor|vendor_observed|estimate)$", exclude_if=lambda value: value is None
    )
    cycle_reset_at: datetime | None = Field(default=None, exclude_if=lambda value: value is None)
    unbudgeted_accounts: int | None = Field(default=None, exclude_if=lambda value: value is None)


class PoolsResponse(DashboardModel):
    generated_at: datetime
    pools: list[PoolSummary] = Field(default_factory=list)
