from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AccountLeaseKind = Literal["response_create", "stream"]


@dataclass
class RuntimeState:
    reset_at: float | None = None
    cooldown_until: float | None = None
    error_rule_cooldown_until: float | None = None
    last_error_at: float | None = None
    last_selected_at: float | None = None
    error_count: int = 0
    version: int = 0
    blocked_at: float | None = None
    health_tier: int = 0
    drain_entered_at: float | None = None
    probe_success_streak: int = 0
    inflight_response_creates: int = 0
    inflight_streams: int = 0
    leased_tokens: float = 0.0
    leases: dict[str, "AccountLease"] | None = None


@dataclass(frozen=True, slots=True)
class AccountLease:
    lease_id: str
    account_id: str
    kind: AccountLeaseKind
    acquired_at: float
    estimated_tokens: float = 0.0
