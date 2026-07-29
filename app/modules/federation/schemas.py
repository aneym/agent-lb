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
