from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.modules.request_logs.schemas import RequestLogEntry
from app.modules.shared.schemas import DashboardModel


class SessionModelRequests(DashboardModel):
    model: str
    requests: int


class SessionAggregate(DashboardModel):
    session_id: str
    provider: str
    useragent_group: str | None = None
    models: list[SessionModelRequests] = Field(default_factory=list)
    sparkline: list[int] = Field(default_factory=list)
    requests: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cost_usd: float
    errors: int
    first_seen: datetime
    last_seen: datetime


class SessionListResponse(DashboardModel):
    sessions: list[SessionAggregate] = Field(default_factory=list)
    total: int


class SessionModelBreakdown(DashboardModel):
    model: str
    requests: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cost_usd: float


class SessionDetailResponse(DashboardModel):
    session: SessionAggregate
    by_model: list[SessionModelBreakdown] = Field(default_factory=list)
    recent_requests: list[RequestLogEntry] = Field(default_factory=list)


class SessionSeriesModel(DashboardModel):
    model: str
    reasoning_effort: str | None = None
    requests: int
    output_tokens: int
    cached_input_tokens: int
    cost_usd: float


class SessionSeriesBucket(DashboardModel):
    bucket_start: datetime
    by_model: list[SessionSeriesModel] = Field(default_factory=list)


class SessionSeat(DashboardModel):
    model: str
    reasoning_effort: str | None = None
    requests: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cost_usd: float
    errors: int


class SessionHistogramBin(DashboardModel):
    label: str
    count: int


class SessionAnalyticsResponse(DashboardModel):
    session: SessionAggregate
    bucket_seconds: int
    series: list[SessionSeriesBucket] = Field(default_factory=list)
    seats: list[SessionSeat] = Field(default_factory=list)
    latency_histogram: list[SessionHistogramBin] = Field(default_factory=list)
    tokens_per_request_histogram: list[SessionHistogramBin] = Field(default_factory=list)
