from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from app.core.utils.time import to_utc_naive
from app.modules.shared.schemas import DashboardModel

SYNC_LIMIT = 2000
Host = Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]{1,32}$")]


class SyncThread(DashboardModel):
    thread_id: str = Field(min_length=1)
    project_id: str | None = Field(default=None, max_length=64)
    project_title: str | None = None
    workspace_root: str | None = None
    title: str | None = None
    provider_instance: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=64)
    parent_thread_id: str | None = None
    root_thread_id: str | None = None
    relationship: str | None = Field(default=None, max_length=32)
    created_by: str | None = Field(default=None, max_length=16)
    creation_source: str | None = Field(default=None, max_length=16)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    archived_at: datetime | None = None
    deleted_at: datetime | None = None

    @model_validator(mode="after")
    def _normalize(self) -> SyncThread:
        self.root_thread_id = self.root_thread_id or self.thread_id
        for name in ("created_at", "updated_at", "archived_at", "deleted_at"):
            value = getattr(self, name)
            if value is not None:
                setattr(self, name, to_utc_naive(value))
        return self


class SyncNativeSession(DashboardModel):
    native_id: str = Field(min_length=1, max_length=64)
    thread_id: str = Field(min_length=1)
    driver: str = Field(min_length=1, max_length=32)
    provider_instance: str | None = Field(default=None, max_length=64)


class SyncRequest(DashboardModel):
    host: Host
    threads: list[SyncThread] = Field(default_factory=list, max_length=SYNC_LIMIT)
    native_sessions: list[SyncNativeSession] = Field(default_factory=list, max_length=SYNC_LIMIT)


class SyncResponse(DashboardModel):
    threads: int
    native_sessions: int
    moved_sessions: int


class NativeSessionRef(DashboardModel):
    native_id: str
    driver: str


class PoolThreadBurn(DashboardModel):
    pool_share_pct: float = 0.0
    account_week_pct: float = 0.0
    pace_multiple: float | None = None
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0


class HourPoint(DashboardModel):
    hour: datetime
    account_week_pct: float


class ThreadBurn(DashboardModel):
    host: str
    thread_id: str
    root_thread_id: str
    title: str | None = None
    project_title: str | None = None
    created_at: datetime | None = None
    last_request_at: datetime | None = None
    archived: bool = False
    deleted: bool = False
    subagent_count: int = 0
    native_sessions: list[NativeSessionRef] = Field(default_factory=list)
    pools: dict[str, PoolThreadBurn] = Field(default_factory=dict)
    models: dict[str, int] = Field(default_factory=dict)
    children: list[ThreadBurn] = Field(default_factory=list)
    hourly: dict[str, list[HourPoint]] | None = None


class PoolShare(DashboardModel):
    pool_share_pct: float = 0.0
    account_week_pct: float = 0.0


class PoolBurn(DashboardModel):
    t3_threads: PoolShare
    t3_unregistered: PoolShare
    outside_t3: PoolShare
    unexplained: PoolShare
    matched_request_pct: float | None = None


class BurnResponse(DashboardModel):
    generated_at: datetime
    window_hours: int
    host: str | None = None
    pools: dict[str, PoolBurn]
    threads: list[ThreadBurn]
