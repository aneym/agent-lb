from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

STICKY_PIN_BATCH_LIMIT = 1000

StickyPinKind = Literal["codex_session", "sticky_thread"]


class StickyPin(BaseModel):
    key: str = Field(min_length=1, max_length=1024)
    kind: StickyPinKind
    account_id: str = Field(min_length=1, max_length=256)
    bound_at: datetime


class StickyPinExchangeRequest(BaseModel):
    instance_id: str = Field(min_length=1, max_length=128)
    seed: bool = False
    pins: list[StickyPin] = Field(default_factory=list, max_length=STICKY_PIN_BATCH_LIMIT)
    since: datetime | None = None
    after_key: str | None = Field(default=None, max_length=1024)


class StickyPinExchangeResponse(BaseModel):
    accepted: int
    pins: list[StickyPin]
    more: bool
    server_now: datetime
