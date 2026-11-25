from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from app.core.utils.time import naive_utc_to_epoch

EXTRA_USAGE_QUOTA_KEY = "anthropic_extra_usage"
_PRIMARY_WINDOW = timedelta(hours=5)


class QuotaReading(Protocol):
    @property
    def used_percent(self) -> float: ...
    @property
    def reset_at(self) -> int | None: ...
    @property
    def recorded_at(self) -> datetime: ...


def extra_usage_blocked(
    tripwire: QuotaReading,
    primary: QuotaReading | None,
    secondary: QuotaReading | None,
    *,
    now: datetime,
    route_paid_extra: bool,
) -> bool:
    now_epoch = naive_utc_to_epoch(now)
    if tripwire.used_percent < 100 or tripwire.reset_at is None or tripwire.reset_at <= now_epoch:
        return False
    primary_recovered = (
        primary is not None and primary.used_percent < 100 and primary.recorded_at > tripwire.recorded_at
    )
    if route_paid_extra or secondary is None:
        return not primary_recovered
    if secondary.used_percent >= 100 or secondary.recorded_at <= tripwire.recorded_at:
        return True
    if primary_recovered or primary is None:
        return False
    return primary.recorded_at > now - _PRIMARY_WINDOW or (
        primary.reset_at is not None and primary.reset_at > now_epoch
    )
