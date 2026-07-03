from __future__ import annotations

from pydantic import BaseModel


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
