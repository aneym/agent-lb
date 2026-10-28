from __future__ import annotations

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


class OauthStartRequest(DashboardModel):
    force_method: str | None = None
    provider: str | None = None
    warmup_hours: float | None = Field(default=None, gt=0, le=168)
    warmup_capacity_percent: float | None = Field(default=None, gt=0, le=100)
    warmup_max_concurrent: int | None = Field(default=None, ge=1, le=32)


class OauthStartResponse(DashboardModel):
    flow_id: str | None = None
    method: str
    authorization_url: str | None = None
    callback_url: str | None = None
    verification_url: str | None = None
    user_code: str | None = None
    device_auth_id: str | None = None
    interval_seconds: int | None = None
    expires_in_seconds: int | None = None


class OauthStatusResponse(DashboardModel):
    status: str
    error_message: str | None = None


class OauthCompleteRequest(DashboardModel):
    flow_id: str | None = None
    device_auth_id: str | None = None
    user_code: str | None = None


class OauthCompleteResponse(DashboardModel):
    status: str


class ManualCallbackRequest(DashboardModel):
    callback_url: str
    flow_id: str | None = None


class ManualCallbackResponse(DashboardModel):
    status: str
    error_message: str | None = None
