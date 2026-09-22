from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.modules.shared.schemas import DashboardModel

POOL_STATUS_OK = "ok"
POOL_STATUS_LOW = "low"
POOL_STATUS_EXHAUSTED = "exhausted"

POOL_SOURCE_SCOPED_MARKER = "scoped_marker"
POOL_SOURCE_WEEKLY_HEURISTIC = "weekly_heuristic"


class PoolSummary(DashboardModel):
    """One routable capacity pool, as the router's `route pick` reads it."""

    id: str
    provider: str
    kind: str
    # Every account the pool draws from, including ones excluded from the
    # numbers below (canceled, deactivated, paused, re-auth).
    accounts: int
    eligible_accounts: int
    # The best single account's remaining percent — what one more request can
    # actually use. Null when no account in the pool is usable.
    headroom_percent: float | None = None
    aggregate_remaining_percent: float | None = None
    # Reset of the window belonging to the account that supplies the headroom.
    reset_at: datetime | None = None
    status: str = Field(pattern=r"^(ok|low|exhausted)$")
    # Fable pool only: which signal the numbers came from.
    source: str | None = None
    # Weekly pools only: the mean weekly (secondary) remaining over usable accounts,
    # not capped by the five-hour window, and the earliest weekly reset among them.
    # `route pick` paces spend against the weekly cycle with these.
    weekly_remaining_percent: float | None = None
    weekly_reset_at: datetime | None = None
    # Mean per-account weekly pace: remaining% minus the share of the 168 h cycle
    # still ahead of that account's own reset. Positive = more budget than time.
    weekly_pace_percent: float | None = None


class PoolsResponse(DashboardModel):
    generated_at: datetime
    pools: list[PoolSummary] = Field(default_factory=list)
