from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.core.identity import RequestIdentity
from app.core.openai.models import ResponseUsage
from app.db.models import Account, AccountLimitWarmup
from app.modules.limit_warmup.constants import _ANTHROPIC_CONTINUOUS_WINDOW


@dataclass(frozen=True, slots=True)
class LimitWarmupSendResult:
    request_id: str
    success: bool
    latency_ms: int
    usage: ResponseUsage | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    upstream_proxy_route_mode: str | None = None
    upstream_proxy_pool_id: str | None = None
    upstream_proxy_endpoint_id: str | None = None
    upstream_proxy_fallback_used: bool | None = None
    upstream_proxy_fail_closed_reason: str | None = None


@dataclass(frozen=True, slots=True)
class LimitWarmupSendOutcome:
    attempt: AccountLimitWarmup
    account: Account
    model: str
    result: LimitWarmupSendResult | None
    error_message: str | None = None


class LimitWarmupSender(Protocol):
    async def send(self, account: Account, *, model: str, prompt: str) -> LimitWarmupSendResult: ...


class LimitWarmupAttemptsRepository(Protocol):
    async def latest_by_account(self, account_ids: list[str]) -> dict[str, AccountLimitWarmup]: ...

    async def claim_continuous_attempt(
        self,
        *,
        account_id: str,
        reset_at: int,
        model: str,
        now: datetime,
        window: str = _ANTHROPIC_CONTINUOUS_WINDOW,
    ) -> AccountLimitWarmup | None: ...

    async def try_create_attempt(
        self,
        *,
        account_id: str,
        window: str,
        reset_at: int,
        model: str,
        attempted_at,
        status: str = "pending",
    ) -> AccountLimitWarmup | None: ...

    async def complete_attempt(
        self,
        attempt_id: int,
        *,
        status: str,
        completed_at,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AccountLimitWarmup | None: ...


class LimitWarmupRequestLogRepository(Protocol):
    async def add_log(
        self,
        account_id: str | None,
        request_id: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        latency_ms: int | None,
        status: str,
        error_code: str | None,
        latency_first_token_ms: int | None = None,
        error_message: str | None = None,
        requested_at: datetime | None = None,
        cached_input_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        reasoning_effort: str | None = None,
        service_tier: str | None = None,
        requested_service_tier: str | None = None,
        actual_service_tier: str | None = None,
        transport: str | None = None,
        api_key_id: str | None = None,
        session_id: str | None = None,
        plan_type: str | None = None,
        source: str | None = None,
        useragent: str | None = None,
        useragent_group: str | None = None,
        failure_phase: str | None = None,
        failure_detail: str | None = None,
        failure_exception_type: str | None = None,
        upstream_status_code: int | None = None,
        upstream_error_code: str | None = None,
        bridge_stage: str | None = None,
        request_kind: str = "normal",
        upstream_proxy_route_mode: str | None = None,
        upstream_proxy_pool_id: str | None = None,
        upstream_proxy_endpoint_id: str | None = None,
        upstream_proxy_fallback_used: bool | None = None,
        upstream_proxy_fail_closed_reason: str | None = None,
        *,
        identity: RequestIdentity | None = None,
    ) -> object: ...
