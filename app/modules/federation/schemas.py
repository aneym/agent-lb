from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.modules.shared.schemas import DashboardModel


class FederationMirrorAccount(BaseModel):
    """Owner-exported view of one owned account. NEVER carries a refresh token."""

    account_id: str
    provider: str
    alias: str | None = None
    email: str
    status: str
    plan_type: str
    chatgpt_account_id: str | None = None
    access_token: str
    expires_at_ms: int | None = None


class FederationMirrorResponse(BaseModel):
    instance_id: str
    accounts: list[FederationMirrorAccount]


class FederationUsageDayRollup(BaseModel):
    day: date
    account_id: str
    provider: str
    requests: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_read_tokens: int = Field(ge=0)
    cost: float = Field(ge=0)
    session_count: int = Field(ge=0)
    last_request_at: datetime | None = None


class FederationPushRequest(BaseModel):
    """A peer LB pushing its owned accounts into this LB (sender owns and refreshes them)."""

    instance_id: str = Field(min_length=1)
    accounts: list[FederationMirrorAccount]


class FederationPushSkip(BaseModel):
    account_id: str
    reason: str


class FederationPushResponse(BaseModel):
    """Receiver's answer to a push. NEVER carries an access or refresh token."""

    source: str
    accepted: list[str]
    skipped: list[FederationPushSkip]
    removed: list[str]
    usage: list[FederationUsageDayRollup]


class FederationUsageReportRequest(BaseModel):
    instance_id: str = Field(min_length=1)
    rollups: list[FederationUsageDayRollup]


class FederationUsageReportResponse(BaseModel):
    instance_id: str
    accepted: int
    reported_at: datetime


class FederationUsageTotals(DashboardModel):
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0


class FederationUsageAccount(DashboardModel):
    account_id: str
    provider: str
    requests: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cost: float
    session_count: int
    last_request_at: datetime | None = None
    reported_at: datetime | None = None


class FederationUsageDay(DashboardModel):
    day: date
    totals: FederationUsageTotals
    accounts: list[FederationUsageAccount]


class FederationUsageInstance(DashboardModel):
    instance_id: str
    totals: FederationUsageTotals
    days: list[FederationUsageDay]


class FederationUsageInstancesResponse(DashboardModel):
    window_days: int
    instances: list[FederationUsageInstance]


class FederationMirrorStatus(DashboardModel):
    enabled: bool
    interval_seconds: int
    last_success_at: datetime | None = None
    last_attempt_at: datetime | None = None
    consecutive_failures: int
    last_error: str | None = None


class FederationUsagePushStatus(DashboardModel):
    last_success_at: datetime | None = None
    last_error: str | None = None


class FederationAccountCounts(DashboardModel):
    owned: int
    mirrored: int


class FederationStatusResponse(DashboardModel):
    local_instance_id: str
    token_configured: bool
    peer_url: str | None = None
    mirror: FederationMirrorStatus
    usage_push: FederationUsagePushStatus
    accounts: FederationAccountCounts


class FederationAuthPayload(BaseModel):
    """Full auth + identity material for a durable token import (checkout/checkin only)."""

    access_token: str
    refresh_token: str
    id_token: str | None = None
    expires_at_ms: int | None = None
    provider: str
    email: str
    alias: str | None = None
    status: str
    plan_type: str
    chatgpt_account_id: str | None = None


class FederationCheckoutRequest(BaseModel):
    account_id: str
    taker_instance_id: str
    nonce: str


class FederationCheckoutResponse(BaseModel):
    account_id: str
    nonce: str
    owner_instance_id: str
    auth: FederationAuthPayload


class FederationCheckoutConfirmRequest(BaseModel):
    nonce: str


class FederationTransferStatusResponse(BaseModel):
    account_id: str
    nonce: str
    state: str


class FederationCheckinRequest(BaseModel):
    caller_instance_id: str
    account_id: str
    nonce: str
    auth: FederationAuthPayload


class FederationCheckoutExecuteRequest(BaseModel):
    account_id: str


class FederationCheckoutExecuteResponse(BaseModel):
    account_id: str
    nonce: str
    owner_instance: str
    confirmed: bool


class FederationCheckinExecuteRequest(BaseModel):
    account_id: str


class FederationCheckinExecuteResponse(BaseModel):
    account_id: str
    nonce: str
    settled: bool


class FederationAbortResponse(BaseModel):
    state: str


class FederationTransferStateResponse(BaseModel):
    state: str


class FederationAbortRequest(BaseModel):
    account_id: str
    direction: str
    caller_instance_id: str


_INSTANCE_ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,64}$"
_REQUEST_LOG_BATCH_LIMIT = 500
# Match RequestLog String(n) columns. Unbounded String/Text columns stay uncapped.
CALLER_USER_MAX_LENGTH = 32
CALLER_USER_SOURCE_MAX_LENGTH = 32
CALLER_MACHINE_MAX_LENGTH = 48
CALLER_MACHINE_SOURCE_MAX_LENGTH = 16
CALLER_SEAT_MAX_LENGTH = 64
ROOM_MAX_LENGTH = 128


class FederationRequestLogRow(BaseModel):
    """One edge request_logs row. No headers, bodies, or tokens."""

    source_row_id: int
    account_id: str | None = None
    provider: str
    api_key_id: str | None = None
    session_id: str | None = None
    client_session_id: str | None = None
    caller_user: str | None = Field(default=None, max_length=CALLER_USER_MAX_LENGTH)
    caller_user_source: str | None = Field(default=None, max_length=CALLER_USER_SOURCE_MAX_LENGTH)
    caller_machine: str | None = Field(default=None, max_length=CALLER_MACHINE_MAX_LENGTH)
    caller_machine_source: str | None = Field(default=None, max_length=CALLER_MACHINE_SOURCE_MAX_LENGTH)
    caller_seat: str | None = Field(default=None, max_length=CALLER_SEAT_MAX_LENGTH)
    room: str | None = Field(default=None, max_length=ROOM_MAX_LENGTH)
    unified_5h_utilization: float | None = None
    unified_7d_utilization: float | None = None
    request_id: str
    request_kind: str
    requested_at: datetime
    model: str
    plan_type: str | None = None
    source: str | None = None
    useragent: str | None = None
    useragent_group: str | None = None
    transport: str | None = None
    service_tier: str | None = None
    requested_service_tier: str | None = None
    actual_service_tier: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_creation_tokens: int | None = None
    cache_read_tokens: int | None = None
    reasoning_tokens: int | None = None
    cost_usd: float | None = None
    reasoning_effort: str | None = None
    latency_ms: int | None = None
    latency_first_token_ms: int | None = None
    status: str
    error_code: str | None = None
    error_message: str | None = None
    failure_phase: str | None = None
    failure_detail: str | None = None
    failure_exception_type: str | None = None
    upstream_status_code: int | None = None
    upstream_error_code: str | None = None
    bridge_stage: str | None = None
    upstream_proxy_route_mode: str | None = None
    upstream_proxy_pool_id: str | None = None
    upstream_proxy_endpoint_id: str | None = None
    upstream_proxy_fallback_used: bool | None = None
    upstream_proxy_fail_closed_reason: str | None = None


class FederationRequestLogsRequest(BaseModel):
    instance_id: str = Field(pattern=_INSTANCE_ID_PATTERN)
    rows: list[FederationRequestLogRow] = Field(max_length=_REQUEST_LOG_BATCH_LIMIT)


class FederationRequestLogsResponse(BaseModel):
    accepted: int
    skipped: int
    max_source_row_id: int
