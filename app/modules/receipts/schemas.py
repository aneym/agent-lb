from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


class Receipt(DashboardModel):
    id: int
    requested_at: datetime
    session_id: str | None
    client_session_id: str | None
    api_key_id: str | None
    api_key_name: str | None
    api_key_prefix: str | None
    account_id: str | None
    provider: str
    pool: str
    model: str
    reasoning_effort: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cache_read_ratio: float | None
    latency_ms: int | None
    latency_first_token_ms: int | None
    status: str
    http_status: int | None
    error_code: str | None
    cost_usd: float | None


class ReceiptsResponse(DashboardModel):
    receipts: list[Receipt]
    total: int
    limit: int
    offset: int


class ReceiptTotals(DashboardModel):
    requests: int
    errors: int
    error_rate: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    tokens: int
    cache_read_ratio: float | None
    cost_usd: float
    p50_latency_ms: int | None
    p95_latency_ms: int | None
    top_error_code: str | None


class ReceiptGroup(DashboardModel):
    key: str | None
    provider: str | None
    requests: int
    errors: int
    error_rate: float
    tokens: int
    cache_read_ratio: float | None
    cost_usd: float


class ReceiptSeries(DashboardModel):
    start: datetime
    requests: int
    by_provider: dict[str, int] = Field(default_factory=dict)


class ReceiptSummary(DashboardModel):
    totals: ReceiptTotals
    groups: list[ReceiptGroup]
    series: list[ReceiptSeries]
