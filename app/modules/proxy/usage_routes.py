from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, Request, Response, Security
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import usage as usage_core
from app.core.auth.dependencies import validate_codex_usage_identity, validate_proxy_api_key, validate_usage_api_key
from app.core.errors import openai_error
from app.core.exceptions import ProxyAuthError
from app.core.openai.model_registry import get_model_registry
from app.db.models import Account, AccountStatus
from app.db.session import get_background_session
from app.dependencies import ProxyContext, get_proxy_context
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeysService
from app.modules.api_keys.service_data import ApiKeyData, ApiKeySelfLimitData
from app.modules.proxy.api_dependencies import proxy_dependencies
from app.modules.proxy.constants import WARMUP_MODES
from app.modules.proxy.model_metadata import (
    allowed_models_for_api_key,
    codex_model_entries,
    codex_model_visibility_allowed_models,
)
from app.modules.proxy.models_api import build_models_response
from app.modules.proxy.request_admission import enforce_request_limits
from app.modules.proxy.reservations import release_reservation
from app.modules.proxy.response_errors import logged_error_json_response
from app.modules.proxy.schemas import (
    CodexModelsResponse,
    ModelListResponse,
    RateLimitStatusPayload,
    V1UsageLimitResponse,
    V1UsageMemberResponse,
    V1UsageMemberWindowResponse,
    V1UsagePoolWindowResponse,
    V1UsageResponse,
    WarmupFailedAccount,
    WarmupRequest,
    WarmupResponse,
    WarmupSkippedAccount,
    WarmupSubmittedAccount,
)
from app.modules.proxy.types import CreditStatusDetailsData, RateLimitStatusPayloadData, RateLimitWindowSnapshotData
from app.modules.team.gate import get_member_self_status
from app.modules.team.service import TeamMemberSelfStatus
from app.modules.usage.mappers import usage_history_to_window_row
from app.modules.usage.repository import UsageRepository

usage_routes_router = APIRouter(prefix="/backend-api/codex")


usage_routes_v1_router = APIRouter(prefix="/v1")


usage_routes_usage_router = APIRouter(prefix="")


@usage_routes_router.get("/models", response_model=CodexModelsResponse)
async def models(
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    response = await build_codex_models_response(api_key)
    return response


@usage_routes_v1_router.get("/models", response_model=ModelListResponse)
async def v1_models(
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    response = await build_models_response(api_key)
    return response


@usage_routes_v1_router.get("/usage", response_model=V1UsageResponse)
async def v1_usage(
    api_key: ApiKeyData = Security(validate_usage_api_key),
) -> V1UsageResponse:
    async with get_background_session() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        usage = await service.get_key_usage_summary_for_self(api_key.id)
        aggregate_limits = await build_aggregate_credit_limits(session)

    if usage is None:
        raise ProxyAuthError("Invalid API key")

    return V1UsageResponse(
        request_count=usage.request_count,
        total_tokens=usage.total_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        total_cost_usd=usage.total_cost_usd,
        limits=[to_v1_usage_limit_response(limit) for limit in usage.limits],
        upstream_limits=ordered_aggregate_limits(aggregate_limits),
        member=to_v1_usage_member_response(await get_member_self_status(api_key)),
    )


async def run_v1_warmup(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = None,
    *,
    mode: str,
) -> Response:
    if mode not in WARMUP_MODES:
        return logged_error_json_response(
            request,
            400,
            openai_error(
                "invalid_request_error",
                "Invalid warmup mode. Supported values: normal, strict, force.",
                error_type="invalid_request_error",
            ),
        )

    try:
        result = await context.service.warmup(mode=mode, headers=request.headers, api_key=api_key)
    except ValueError as exc:
        return logged_error_json_response(
            request,
            400,
            openai_error(
                "invalid_request_error",
                str(exc),
                error_type="invalid_request_error",
            ),
        )

    response = WarmupResponse(
        mode=result.mode,
        total_accounts=result.total_accounts,
        submitted=[
            WarmupSubmittedAccount(
                account_id=entry.account_id,
                request_id=entry.request_id,
                model=entry.model,
            )
            for entry in result.submitted
        ],
        skipped=[
            WarmupSkippedAccount(
                account_id=entry.account_id,
                reason=entry.reason,
            )
            for entry in result.skipped
        ],
        failed=[
            WarmupFailedAccount(
                account_id=entry.account_id,
                error_code=entry.error_code,
                error_message=entry.error_message,
            )
            for entry in result.failed
        ],
    )
    return JSONResponse(content=response.model_dump(mode="json"))


@usage_routes_v1_router.post("/warmup", response_model=WarmupResponse)
async def v1_warmup(
    request: Request,
    payload: WarmupRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await run_v1_warmup(
        request,
        context,
        api_key,
        mode=payload.mode.strip().lower(),
    )


@usage_routes_v1_router.post("/warmup/{mode}", response_model=WarmupResponse)
async def v1_warmup_by_mode(
    request: Request,
    mode: str,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await run_v1_warmup(
        request,
        context,
        api_key,
        mode=mode.strip().lower(),
    )


def ordered_aggregate_limits(aggregate_limits: dict[str, V1UsageLimitResponse]) -> list[V1UsageLimitResponse]:
    return [limit for window in ("5h", "7d", "monthly") if (limit := aggregate_limits.get(window)) is not None]


def to_v1_usage_member_response(status: TeamMemberSelfStatus | None) -> V1UsageMemberResponse | None:
    if status is None:
        return None
    return V1UsageMemberResponse(
        id=status.id,
        name=status.name,
        status=status.status,
        gate=status.gate,
        allowed_models=status.allowed_models,
        pool_share_percent=status.pool_share_percent,
        pool_windows=[
            V1UsagePoolWindowResponse(
                window=window.window,
                used_percent=window.used_percent,
                limit_percent=window.limit_percent,
                reset_at=window.reset_at.isoformat() + "Z",
            )
            for window in status.pool_windows
        ],
        windows=[
            V1UsageMemberWindowResponse(
                window=window.window,
                cost_cap_usd=window.cost_cap_usd,
                token_cap=window.token_cap,
                cost_usd=window.cost_usd,
                tokens=window.tokens,
                window_start=window.window_start.isoformat() + "Z",
                window_end=window.window_end.isoformat() + "Z",
            )
            for window in status.windows
        ],
    )


def to_v1_usage_limit_response(limit: ApiKeySelfLimitData) -> V1UsageLimitResponse:
    current_value = max(0, min(limit.current_value, limit.max_value))
    return V1UsageLimitResponse(
        limit_type=limit.limit_type,
        limit_window=limit.limit_window,
        max_value=limit.max_value,
        current_value=current_value,
        remaining_value=max(0, limit.max_value - current_value),
        model_filter=limit.model_filter,
        reset_at=limit.reset_at.isoformat() + "Z",
        source=limit.source,
    )


async def build_codex_usage_payload_for_api_key(api_key: ApiKeyData) -> RateLimitStatusPayloadData:
    async with get_background_session() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        usage = await service.get_key_usage_summary_for_self(api_key.id)

    if usage is None:
        raise ProxyAuthError("Invalid API key")

    key_limits = [to_v1_usage_limit_response(limit) for limit in usage.limits]
    primary_credit_limit = select_codex_usage_limit(key_limits, "5h") or select_codex_usage_limit(key_limits, "daily")
    secondary_credit_limit = select_codex_usage_limit(key_limits, "7d") or select_codex_usage_limit(
        key_limits, "weekly"
    )
    monthly_credit_limit = select_codex_usage_limit(key_limits, "monthly")

    return RateLimitStatusPayloadData(
        plan_type="api_key",
        rate_limit=proxy_dependencies.rate_limit_details(
            codex_usage_window_snapshot(primary_credit_limit),
            codex_usage_window_snapshot(secondary_credit_limit),
            codex_usage_window_snapshot(monthly_credit_limit),
        ),
        credits=codex_usage_credit_snapshot(primary_credit_limit, secondary_credit_limit, monthly_credit_limit),
    )


def select_codex_usage_limit(
    limits: list[V1UsageLimitResponse],
    window: str,
) -> V1UsageLimitResponse | None:
    candidates = [
        limit
        for limit in limits
        if limit.limit_window == window and limit.model_filter is None and limit.limit_type == "credits"
    ]
    return candidates[0] if candidates else None


def codex_usage_window_snapshot(limit: V1UsageLimitResponse | None) -> RateLimitWindowSnapshotData | None:
    if limit is None or limit.max_value <= 0:
        return None
    reset_at = datetime.fromisoformat(limit.reset_at.replace("Z", "+00:00"))
    reset_epoch = int(reset_at.timestamp())
    now_epoch = int(time.time())
    used_percent = max(0, min(100, int((limit.current_value / limit.max_value) * 100)))
    window_seconds = {"5h": 18000, "daily": 86400, "7d": 604800, "weekly": 604800, "monthly": 2592000}.get(
        limit.limit_window
    )
    return RateLimitWindowSnapshotData(
        used_percent=used_percent,
        limit_window_seconds=window_seconds,
        reset_after_seconds=max(0, reset_epoch - now_epoch),
        reset_at=reset_epoch,
    )


def codex_usage_credit_snapshot(
    primary_limit: V1UsageLimitResponse | None,
    secondary_limit: V1UsageLimitResponse | None,
    monthly_limit: V1UsageLimitResponse | None = None,
) -> CreditStatusDetailsData | None:
    preferred = monthly_limit or secondary_limit or primary_limit
    if preferred is None or preferred.limit_type != "credits":
        return None
    return CreditStatusDetailsData(
        has_credits=preferred.remaining_value > 0,
        unlimited=False,
        balance=str(preferred.remaining_value),
        approx_local_messages=None,
        approx_cloud_messages=None,
    )


async def build_aggregate_credit_limits(session: AsyncSession) -> dict[str, V1UsageLimitResponse]:
    usage_repository = UsageRepository(session)
    primary_latest = await usage_repository.latest_by_account(window="primary")
    secondary_latest = await usage_repository.latest_by_account(window="secondary")
    monthly_latest = await usage_repository.latest_by_account(window="monthly")

    primary_rows = [usage_history_to_window_row(entry) for entry in primary_latest.values()]
    secondary_rows = [usage_history_to_window_row(entry) for entry in secondary_latest.values()]
    monthly_rows = [usage_history_to_window_row(entry) for entry in monthly_latest.values()]
    primary_rows, secondary_rows = usage_core.normalize_weekly_only_rows(primary_rows, secondary_rows)

    account_ids = (
        {row.account_id for row in primary_rows}
        | {row.account_id for row in secondary_rows}
        | {row.account_id for row in monthly_rows}
    )
    if not account_ids:
        return {}

    account_map = {account.id: account for account in await load_accounts_by_id(session, account_ids)}
    if not account_map:
        return {}

    active_account_ids = set(account_map)
    primary_rows = [row for row in primary_rows if row.account_id in active_account_ids]
    secondary_rows = [row for row in secondary_rows if row.account_id in active_account_ids]
    monthly_rows = [row for row in monthly_rows if row.account_id in active_account_ids]
    limits: dict[str, V1UsageLimitResponse] = {}

    for window_key, rows, label in (
        ("primary", primary_rows, "5h"),
        ("secondary", secondary_rows, "7d"),
        ("monthly", monthly_rows, "monthly"),
    ):
        if not rows:
            continue
        summary = usage_core.summarize_usage_window(rows, account_map, window_key)
        max_value = max(0, int(round(summary.capacity_credits or 0.0)))
        if max_value <= 0:
            continue
        if summary.reset_at is None:
            continue
        current_value = max(0, min(int(round(summary.used_credits or 0.0)), max_value))
        limits[label] = V1UsageLimitResponse(
            limit_type="credits",
            limit_window=label,
            max_value=max_value,
            current_value=current_value,
            remaining_value=max(0, max_value - current_value),
            model_filter=None,
            reset_at=datetime.fromtimestamp(summary.reset_at, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            source="aggregate",
        )

    return limits


async def load_accounts_by_id(session: AsyncSession, account_ids: set[str]) -> list[Account]:
    if not account_ids:
        return []
    result = await session.execute(
        select(Account).where(
            Account.id.in_(account_ids),
            Account.status.notin_((AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED, AccountStatus.PAUSED)),
        )
    )
    return list(result.scalars().all())


async def build_codex_models_response(api_key: ApiKeyData | None) -> Response:
    reservation = await enforce_request_limits(
        api_key,
        request_model=None,
        request_service_tier=None,
    )

    allowed_models = allowed_models_for_api_key(api_key)
    visibility_allowed_models = codex_model_visibility_allowed_models(api_key)

    registry = get_model_registry()
    models = registry.get_models_with_fallback()

    if not models:
        await release_reservation(reservation)
        return JSONResponse(content=CodexModelsResponse(models=[]).model_dump(mode="json"))

    entries = codex_model_entries(models, allowed_models, visibility_allowed_models)
    await release_reservation(reservation)
    return JSONResponse(content=CodexModelsResponse(models=entries).model_dump(mode="json"))


@usage_routes_usage_router.get("/api/codex/usage/", response_model=RateLimitStatusPayload, include_in_schema=False)
@usage_routes_usage_router.get("/api/codex/usage", response_model=RateLimitStatusPayload)
async def codex_usage(
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Depends(validate_codex_usage_identity),
) -> RateLimitStatusPayload:
    payload = (
        await build_codex_usage_payload_for_api_key(api_key)
        if api_key is not None
        else await context.service.get_rate_limit_payload()
    )
    return RateLimitStatusPayload.from_data(payload)
