from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from app.core.anthropic.response_observability import is_model_mismatch
from app.core.identity import attach_member, get_request_identity
from app.core.providers.gemini import GEMINI_PROVIDER_NAME
from app.core.resilience.circuit_breaker import CircuitBreaker
from app.core.utils.request_id import get_request_id
from app.db.models import Account
from app.db.session import get_background_session
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeysService
from app.modules.api_keys.service_data import ApiKeyData, ApiKeyRequestUsageBudget, ApiKeyUsageReservationData
from app.modules.proxy.gemini_stream import GeminiResponseMetadata, GeminiUsage
from app.modules.proxy.gemini_upstream import GeminiUpstreamError
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.proxy.runtime_state import AccountLease
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.quota_planner.repository import QuotaPlannerRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository

logger = logging.getLogger(__name__)

_EFFORT_SUFFIXES = ("-minimal", "-low", "-medium", "-high", "-xhigh")


@asynccontextmanager
async def gemini_repo_factory() -> AsyncIterator[ProxyRepositories]:
    async with get_background_session() as session:
        yield ProxyRepositories(
            accounts=AccountsRepository(session),
            usage=UsageRepository(session),
            request_logs=RequestLogsRepository(session),
            sticky_sessions=StickySessionsRepository(session),
            api_keys=ApiKeysRepository(session),
            additional_usage=AdditionalUsageRepository(session),
            quota_planner=QuotaPlannerRepository(session),
        )


@asynccontextmanager
async def gemini_accounts_scope() -> AsyncIterator[AccountsRepository]:
    async with gemini_repo_factory() as repos:
        yield repos.accounts


def scoped_account_ids(api_key: ApiKeyData | None) -> set[str] | None:
    if api_key is None or not api_key.account_assignment_scope_enabled:
        return None
    return set(api_key.assigned_account_ids)


async def release_gemini_resources(
    load_balancer: Any,
    lease: AccountLease | None,
    circuit_breaker: CircuitBreaker | None,
    circuit_probe: bool,
) -> None:
    if circuit_probe and circuit_breaker is not None:
        await circuit_breaker.release_half_open_probe()
    if lease is not None:
        await load_balancer.release_account_lease(lease)


@dataclass(frozen=True, slots=True)
class GeminiLogEntry:
    api_key: ApiKeyData | None
    account: Account | None
    model: str
    reasoning_effort: str | None
    useragent: str | None
    usage: GeminiUsage | None
    latency_ms: int
    latency_first_token_ms: int | None
    status: str
    session_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    upstream_status_code: int | None = None
    response_model: str | None = None
    stop_reason: str | None = None
    stop_details_category: str | None = None


def _strip_gemini_effort_suffix(model: str) -> str:
    normalized = model.strip().lower().removeprefix("models/")
    for suffix in _EFFORT_SUFFIXES:
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)]
    return normalized


def observe_gemini_response(entry: GeminiLogEntry, request_id: str) -> None:
    account_id = entry.account.id if entry.account else "-"
    response_model = entry.response_model
    if response_model is not None and is_model_mismatch(
        _strip_gemini_effort_suffix(entry.model),
        _strip_gemini_effort_suffix(response_model),
    ):
        logger.warning(
            "gemini_response_model_mismatch request_id=%s account_id=%s provider=%s "
            "requested_model=%s response_model=%s",
            request_id,
            account_id,
            GEMINI_PROVIDER_NAME,
            entry.model,
            response_model,
        )
    if entry.stop_details_category is not None:
        logger.warning(
            "gemini_upstream_safety_stop request_id=%s account_id=%s provider=%s "
            "requested_model=%s response_model=%s stop_reason=%s stop_details_category=%s",
            request_id,
            account_id,
            GEMINI_PROVIDER_NAME,
            entry.model,
            response_model or "-",
            entry.stop_reason or "-",
            entry.stop_details_category,
        )


def gemini_usage_budget(body: dict[str, Any], raw_size: int) -> ApiKeyRequestUsageBudget:
    config = body.get("generationConfig")
    max_output = config.get("maxOutputTokens") if isinstance(config, dict) else None
    return ApiKeyRequestUsageBudget(
        input_tokens=max(1, raw_size // 4),
        output_tokens=int(max_output) if isinstance(max_output, int) and max_output > 0 else None,
    )


async def record_gemini_circuit_outcome(
    circuit_breaker: CircuitBreaker | None,
    *,
    error: GeminiUpstreamError | None = None,
    cancelled: bool = False,
) -> None:
    if circuit_breaker is None or cancelled:
        return
    if error is None:
        await circuit_breaker.record_success()
        return
    if error.kind == "transient" or error.status_code >= 500:
        await circuit_breaker.record_failure(error)
    elif error.kind == "invalid_request" and error.status_code != 403:
        await circuit_breaker.record_success()


async def record_gemini_request(entry: GeminiLogEntry) -> None:
    request_id = get_request_id() or ""
    observe_gemini_response(entry, request_id)
    try:
        identity = await attach_member(get_request_identity(), entry.api_key.member_id if entry.api_key else None)
        async with get_background_session() as session:
            await RequestLogsRepository(session).add_log(
                identity=identity,
                account_id=entry.account.id if entry.account else None,
                api_key_id=entry.api_key.id if entry.api_key else None,
                request_id=request_id,
                model=entry.model,
                reasoning_effort=entry.reasoning_effort,
                input_tokens=entry.usage.input_tokens if entry.usage else None,
                output_tokens=entry.usage.output_tokens if entry.usage else None,
                cached_input_tokens=entry.usage.cached_input_tokens if entry.usage else None,
                reasoning_tokens=entry.usage.reasoning_tokens if entry.usage else None,
                latency_ms=entry.latency_ms,
                latency_first_token_ms=entry.latency_first_token_ms,
                status=entry.status,
                error_code=entry.error_code,
                error_message=entry.error_message,
                upstream_status_code=entry.upstream_status_code,
                plan_type=entry.account.plan_type if entry.account else None,
                provider=GEMINI_PROVIDER_NAME,
                transport="http",
                useragent=entry.useragent,
                session_id=entry.session_id,
                client_session_id=entry.session_id,
                response_model=entry.response_model,
                stop_reason=entry.stop_reason,
                stop_details_category=entry.stop_details_category,
            )
    except Exception:
        logger.warning("Failed to persist Gemini request log request_id=%s", request_id, exc_info=True)


async def record_gemini_failure(
    api_key: ApiKeyData | None,
    account: Account,
    model: str,
    effort: str,
    useragent: str | None,
    exc: GeminiUpstreamError | None,
    started_at: float,
    session_id: str | None,
) -> None:
    await record_gemini_request(
        GeminiLogEntry(
            api_key=api_key,
            account=account,
            model=model,
            reasoning_effort=effort,
            useragent=useragent,
            usage=None,
            latency_ms=int((time.monotonic() - started_at) * 1000),
            latency_first_token_ms=None,
            status="error",
            session_id=session_id,
            error_code=f"upstream_{exc.kind}" if exc else "client_cancelled",
            error_message=exc.message if exc else None,
            upstream_status_code=exc.status_code if exc else None,
        )
    )


async def record_gemini_finish(
    *,
    api_key: ApiKeyData | None,
    account: Account,
    model: str,
    reasoning_effort: str,
    useragent: str | None,
    usage: GeminiUsage | None,
    started_at: float,
    first_token_at: float | None,
    session_id: str | None,
    error: GeminiUpstreamError | None,
    cancelled: bool,
    metadata: GeminiResponseMetadata | None,
) -> None:
    first_ms = int((first_token_at - started_at) * 1000) if first_token_at is not None else None
    status = "error" if error is not None or cancelled else "success"
    await record_gemini_request(
        GeminiLogEntry(
            api_key=api_key,
            account=account,
            model=model,
            reasoning_effort=reasoning_effort,
            useragent=useragent,
            usage=usage,
            latency_ms=int((time.monotonic() - started_at) * 1000),
            latency_first_token_ms=first_ms,
            status=status,
            session_id=session_id,
            error_code=f"upstream_{error.kind}" if error is not None else ("client_cancelled" if cancelled else None),
            error_message=error.message if error is not None else None,
            upstream_status_code=error.status_code if error is not None else None,
            response_model=metadata.response_model if metadata is not None else None,
            stop_reason=metadata.stop_reason if metadata is not None else None,
            stop_details_category=metadata.stop_details_category if metadata is not None else None,
        )
    )


async def settle_gemini_reservation(
    reservation: ApiKeyUsageReservationData | None, *, model: str, usage: GeminiUsage | None
) -> None:
    if reservation is None:
        return
    try:
        async with get_background_session() as session:
            service = ApiKeysService(ApiKeysRepository(session))
            if usage is None or (usage.input_tokens == 0 and usage.output_tokens == 0):
                await service.release_usage_reservation(reservation.reservation_id)
                return
            await service.finalize_usage_reservation(
                reservation.reservation_id,
                model=model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_input_tokens=usage.cached_input_tokens,
                service_tier=None,
            )
    except Exception:
        logger.warning(
            "Failed to settle Gemini key reservation reservation_id=%s", reservation.reservation_id, exc_info=True
        )
