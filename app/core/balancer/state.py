from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from app.db.models import AccountStatus

SECONDS_PER_DAY = 60 * 60 * 24
SECONDS_PER_HOUR = 60 * 60
SECONDS_PER_WEEK = 7 * SECONDS_PER_DAY
UNKNOWN_RESET_BUCKET_DAYS = 10_000
UNKNOWN_RESET_FALLBACK_SECONDS = 7 * SECONDS_PER_DAY
RELATIVE_AVAILABILITY_MIN_DIVISOR_SECONDS = 5 * 60
RELATIVE_AVAILABILITY_MIN_WEIGHT_FRACTION = 0.1
DEFAULT_RELATIVE_AVAILABILITY_POWER = 2.0
DEFAULT_RELATIVE_AVAILABILITY_TOP_K = 5
RoutingStrategy = Literal[
    "usage_weighted",
    "round_robin",
    "capacity_weighted",
    "relative_availability",
    "fill_first",
    "sequential_drain",
    "reset_drain",
    "single_account",
]
TrafficClass = Literal["foreground", "opportunistic"]
UsageWeightedOrder = Literal["secondary_first", "primary_first"]
ResetPreferenceWindow = Literal["primary", "secondary"]
UNKNOWN_PLAN_FALLBACK = "free"
CAPACITY_PLAN_ALIASES = {
    "education": "edu",
    "k12": "edu",
    "guest": "free",
    "go": "free",
    "free_workspace": "free",
    "quorum": "free",
    "unknown": "free",
}
HEALTH_TIER_HEALTHY = 0
HEALTH_TIER_DRAINING = 1
HEALTH_TIER_PROBING = 2
DRAIN_PRIMARY_THRESHOLD_PCT = 85.0
DRAIN_SECONDARY_THRESHOLD_PCT = 90.0
DRAIN_ERROR_WINDOW_SECONDS = 60.0
DRAIN_ERROR_COUNT_THRESHOLD = 2
PROBE_QUIET_SECONDS = 60.0
PROBE_SUCCESS_STREAK_REQUIRED = 3
ROUTING_POLICY_NORMAL = "normal"
ROUTING_POLICY_BURN_FIRST = "burn_first"
ROUTING_POLICY_PRESERVE = "preserve"
TRAFFIC_CLASS_FOREGROUND = "foreground"
TRAFFIC_CLASS_OPPORTUNISTIC = "opportunistic"
PRESERVE_MIN_WEEKLY_FLOOR_PCT = 5.0
PRESERVE_MIN_SHORT_WINDOW_FLOOR_PCT = 10.0
NORMAL_LAST_ACCOUNT_EMERGENCY_FLOOR_PCT = 5.0
RECENT_FOREGROUND_ACTIVITY_SECONDS = 30 * 60


@dataclass
class AccountState:
    account_id: str
    status: AccountStatus
    used_percent: float | None = None
    reset_at: float | None = None
    primary_reset_at: int | None = None
    blocked_at: float | None = None
    cooldown_until: float | None = None
    error_rule_cooldown_until: float | None = None
    secondary_used_percent: float | None = None
    secondary_reset_at: int | None = None
    last_error_at: float | None = None
    last_selected_at: float | None = None
    error_count: int = 0
    deactivation_reason: str | None = None
    plan_type: str | None = None
    capacity_credits: float | None = None
    health_tier: int = 0
    priority_used_percent: float | None = None
    priority_secondary_used_percent: float | None = None
    priority_reset_at: int | None = None
    priority_capacity_credits: float | None = None
    limit_scoped_usage: bool = False
    inflight_response_creates: int = 0
    inflight_streams: int = 0
    leased_tokens: float = 0.0
    routing_policy: str = ROUTING_POLICY_NORMAL
    ignore_standard_quota: bool = False

    raw_used_percent: float | None = None
    raw_secondary_used_percent: float | None = None


@dataclass
class SelectionResult:
    account: AccountState | None
    error_message: str | None
    excluded_accounts: list[ExcludedAccount] = field(default_factory=list)
    retry_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ExcludedAccount:
    account_id: str
    status: str
    reset_at: datetime | None
    cooldown_until: datetime | None
    deactivation_reason: str | None
    plan_type: str | None
    transient_backoff_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class RoutingCost:
    total: float = 0.0
    reason: str | None = None


RoutingCostsByAccount = dict[str, RoutingCost]
