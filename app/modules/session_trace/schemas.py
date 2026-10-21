from datetime import datetime

from app.modules.shared.schemas import DashboardModel


class TraceParts(DashboardModel):
    system: int = 0
    tools: int = 0
    history: int = 0
    tool_results: int = 0


class TraceRequest(DashboardModel):
    log_id: int
    request_id: str
    requested_at: datetime
    provider: str
    model: str
    status: str
    latency_ms: int | None
    latency_first_token_ms: int | None
    prompt_tokens: int
    uncached_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    output_tokens: int
    cache_read_ratio: float | None
    cache_kind: str | None
    body_schema: str | None
    body_bytes: int | None
    est_parts: TraceParts | None
    part_chars: TraceParts | None
    estimate_ratio: float | None


class TraceTotals(DashboardModel):
    requests: int
    measured: int
    coverage: float
    prompt_tokens: int
    cache_read_tokens: int
    latency_ms: int
    est_parts: TraceParts


class SessionTraceResponse(DashboardModel):
    session_id: str
    requests: list[TraceRequest]
    totals: TraceTotals
