from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


class Pick(DashboardModel):
    seat: str | None = None
    model: str | None = None
    subagent_type: str | None = None


class Candidate(DashboardModel):
    seat: str | None = None
    model: str | None = None
    score: float | None = None
    excluded_reason: str | None = None


class Outcome(DashboardModel):
    state: Literal["ok", "failed", "open"]
    duration_s: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    error: str | None = None
    match: Literal["exact", "ambiguous", "none"] = "none"


class Decision(DashboardModel):
    id: str
    ts: datetime
    session_id: str | None = None
    task_class: str | None = None
    kind: Literal["dispatch", "of_decision"]
    pick: Pick | None = None
    candidates: list[Candidate] = Field(default_factory=list)
    abstained: bool = False
    recheck: str | None = None
    fallback: str | None = None
    decider_ms: float | None = None
    decider_cost_usd: float | None = None
    policy_version: str | int | None = None
    outcome: Outcome


class Counts(DashboardModel):
    total: int = 0
    fallbacks: int = 0
    no_pick: int = 0
    failed: int = 0


class DecisionsResponse(DashboardModel):
    decisions: list[Decision] = Field(default_factory=list)
    counts: Counts
    ledger_path: str
    truncated: bool = False
