from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from app.db.models import LimitType


@dataclass(frozen=True, slots=True)
class ReservationResult:
    success: bool
    limit_id: int
    current_value: int | None
    max_value: int | None
    reset_at: datetime | None


@dataclass(frozen=True, slots=True)
class UsageReservationItemData:
    limit_id: int
    limit_type: LimitType
    reserved_delta: int
    expected_reset_at: datetime
    actual_delta: int | None = None


@dataclass(frozen=True, slots=True)
class UsageReservationData:
    reservation_id: str
    api_key_id: str
    model: str
    status: str
    items: list[UsageReservationItemData]


@dataclass(frozen=True, slots=True)
class ApiKeyUsageSummary:
    request_count: int
    total_tokens: int
    cached_input_tokens: int
    total_cost_usd: float


@dataclass(frozen=True, slots=True)
class ApiKeyTrendBucket:
    bucket_epoch: int
    total_tokens: int
    total_cost_usd: float


@dataclass(frozen=True, slots=True)
class ApiKeyUsageTotals:
    total_requests: int
    total_tokens: int
    cached_input_tokens: int
    total_cost_usd: float
    account_costs: list["ApiKeyAccountCost"] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ApiKeyAccountCost:
    account_id: str | None
    email: str | None
    cost_usd: float
    is_deleted: bool = False


class _Unset(Enum):
    UNSET = "UNSET"


_UNSET = _Unset.UNSET


_EXPIRED_LIMIT_RESET_BATCH_SIZE = 500


_STALE_USAGE_RESERVATION_RELEASE_BATCH_SIZE = 500
