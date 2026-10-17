from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.core.clients.anthropic_resets import ResetStatus


class LiveAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    usable: bool
    primary_remaining_pct: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    primary_reset_at: AwareDatetime | None = None
    weekly_remaining_pct: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    weekly_reset_at: AwareDatetime | None = None
    burn_rate_pct_per_hour: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    reset_grants_available: int = Field(default=0, ge=0)
    next_grant_id: str | None = None
    grant_expires_at: AwareDatetime | None = None
    redeemable_now: bool = False
    reset_status: ResetStatus | None = None
    inventory_available: bool = True

    def remaining(self) -> float | None:
        windows = [v for v in (self.primary_remaining_pct, self.weekly_remaining_pct) if v is not None]
        return min(windows) if windows else None


class WindowSample(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str
    window: Literal["primary", "weekly"]
    reset_at: AwareDatetime
    remaining_pct: float = Field(ge=0, le=100, allow_inf_nan=False)
    observed_at: AwareDatetime


class BlockedAccount(BaseModel):
    id: str
    reset_at: AwareDatetime


class CodexSummary(BaseModel):
    usable_count: int = Field(default=0, ge=0)
    banked_resets_total: int = Field(default=0, ge=0)


class PolicyState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    live_accounts: list[LiveAccount] = Field(default_factory=list)
    blocked_accounts: list[BlockedAccount] = Field(default_factory=list)
    codex: CodexSummary = Field(default_factory=CodexSummary)


class Decision(BaseModel):
    action: Literal["redeem", "hold"]
    account_id: str | None = None
    grant_id: str | None = None
    reason: str
    summary: str


def decide(state: PolicyState, now: datetime) -> Decision:
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")

    def result(reason: str, account: LiveAccount | None = None) -> Decision:
        action = "redeem" if account else "hold"
        return Decision(
            action=action,
            account_id=account.id if account else None,
            grant_id=account.next_grant_id if account else None,
            reason=reason,
            summary=f"{action} {reason}; codex usable={state.codex.usable_count} "
            f"banked_resets={state.codex.banked_resets_total}",
        )

    live = [account for account in state.live_accounts if account.usable]
    if not live:
        return result("no_live_accounts")
    for account in live:
        remaining = account.remaining()
        if remaining is None:
            return result(f"unknown_remaining:{account.id}")
        if remaining >= 5:
            return result(f"above_threshold:{account.id}={remaining:g}")
    for account in live:
        if not account.inventory_available:
            return result(f"inventory_unavailable:{account.id}")
    openings = [account.reset_at for account in state.blocked_accounts]
    if not openings:
        openings = [
            reset
            for account in live
            for remaining, reset in (
                (account.primary_remaining_pct, account.primary_reset_at),
                (account.weekly_remaining_pct, account.weekly_reset_at),
            )
            if remaining is not None and reset is not None
        ]
    if not openings:
        return result("unknown_next_opening")
    if any(account.burn_rate_pct_per_hour is None for account in live):
        return result("no_burn_sample")
    burn = sum(account.burn_rate_pct_per_hour or 0 for account in live)
    runway = sum(account.remaining() or 0 for account in live) / burn if burn else math.inf
    opens_in = max(0.0, (min(openings) - now).total_seconds() / 3600)
    if runway >= opens_in:
        return result(f"can_last:{runway:g}h>={opens_in:g}h")
    candidates = []
    for account in live:
        if account.reset_status is not None:
            grant = account.reset_status.usable_grant(now)
            if grant is None:
                continue
            account = account.model_copy(update={"next_grant_id": grant.id, "grant_expires_at": grant.ends_at})
        elif not (account.redeemable_now and account.reset_grants_available and account.next_grant_id):
            continue
        if account.grant_expires_at is not None and account.grant_expires_at <= now:
            continue
        candidates.append(account)
    if not candidates:
        return result("no_grant")
    chosen = min(
        candidates,
        key=lambda a: (
            a.grant_expires_at.timestamp() if a.grant_expires_at else math.inf,
            a.remaining(),
            a.id,
        ),
    )
    return result(f"cannot_last:{runway:g}h<{opens_in:g}h", chosen)
