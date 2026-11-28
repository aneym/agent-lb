from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Protocol

from app.core.anthropic.quota_headers import parse_reset_epoch
from app.core.balancer.types import UpstreamError
from app.core.utils.time import naive_utc_to_epoch

EXTRA_USAGE_QUOTA_KEY = "anthropic_extra_usage"
_PRIMARY_WINDOW = timedelta(hours=5)
_UNIFIED_OVERAGE_IN_USE_HEADER = "anthropic-ratelimit-unified-overage-in-use"
_UNIFIED_STATUS_HEADERS = (
    "anthropic-ratelimit-unified-status",
    "anthropic-ratelimit-unified-5h-status",
    "anthropic-ratelimit-unified-7d-status",
)
_UNIFIED_ALLOWED_STATUSES = frozenset({"allowed", "allowed_warning"})


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


def _header_value(headers: Mapping[str, str], name: str) -> str | None:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def extra_usage_tripwire_header(headers: Mapping[str, str]) -> str | None:
    overage_in_use = _header_value(headers, _UNIFIED_OVERAGE_IN_USE_HEADER)
    if isinstance(overage_in_use, str) and overage_in_use.strip().lower() == "true":
        return _UNIFIED_OVERAGE_IN_USE_HEADER
    for name in _UNIFIED_STATUS_HEADERS:
        value = _header_value(headers, name)
        if value is None:
            continue
        normalized = value.strip().lower()
        if normalized and normalized not in _UNIFIED_ALLOWED_STATUSES:
            return name
    return None


def extra_usage_tripwire_error(headers: Mapping[str, str]) -> UpstreamError | None:
    if extra_usage_tripwire_header(headers) is None:
        return None
    error = UpstreamError(message="Anthropic unified limit exhausted; upstream is billing extra-usage credits")
    reset_at = parse_reset_epoch(_header_value(headers, "anthropic-ratelimit-unified-reset"))
    if reset_at is None:
        reset_at = parse_reset_epoch(_header_value(headers, "anthropic-ratelimit-unified-5h-reset"))
    if reset_at is not None:
        error["resets_at"] = reset_at
    return error
