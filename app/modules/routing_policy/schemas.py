from __future__ import annotations

from datetime import datetime
from typing import Any

from app.modules.shared.schemas import DashboardModel


class PolicyCounts(DashboardModel):
    classes: int
    options: int
    aliases: int
    retired: int


class PolicySummary(DashboardModel):
    version: int
    state: str
    source: str
    summary: str
    created_at: datetime
    created_by: str
    approved_by: str | None
    approved_at: datetime | None
    counts: PolicyCounts


class PolicyDiff(DashboardModel):
    path: str
    before: Any = None
    after: Any = None


class PolicyDetail(PolicySummary):
    routing_table: dict[str, Any]
    decider: dict[str, Any]
    diff: list[PolicyDiff]


class PolicyVersions(DashboardModel):
    active_version: int | None
    versions: list[PolicySummary]


class DraftCreate(DashboardModel):
    summary: str
    routing_table: dict[str, Any] | None = None
    decider: dict[str, Any] | None = None


class DraftPatch(DashboardModel):
    summary: str | None = None
    routing_table: dict[str, Any] | None = None
    decider: dict[str, Any] | None = None
