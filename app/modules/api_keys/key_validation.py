from __future__ import annotations

import json
import secrets
from datetime import datetime
from hashlib import sha256

from app.core.utils.time import to_utc_naive
from app.db.request_models import ApiKey, ApiKeyLimit, LimitType, LimitWindow
from app.modules.api_keys.limit_windows import advance_limit_reset, limit_window_delta, next_limit_reset
from app.modules.api_keys.policy_validation import (
    _deserialize_allowed_reasoning_efforts,
    _get_usage_sections_with_default,
    _normalize_transport_policy_override_lenient,
)
from app.modules.api_keys.repository_data import ApiKeyUsageSummary
from app.modules.api_keys.repository_protocol import ApiKeysRepositoryProtocol
from app.modules.api_keys.service_data import (
    _SUPPORTED_REASONING_EFFORTS,
    _SUPPORTED_SERVICE_TIERS,
    _SUPPORTED_TRAFFIC_CLASSES,
    TRAFFIC_CLASS_FOREGROUND,
    ApiKeyCreatedData,
    ApiKeyData,
    ApiKeyInvalidError,
    ApiKeyUsageSummaryData,
    ApiKeyValidationError,
    LimitRuleData,
    LimitRuleInput,
    PooledCreditData,
)


async def _new_limit_input_to_backfilled_row(
    submitted: LimitRuleInput, key_id: str, now: datetime, repository: ApiKeysRepositoryProtocol
) -> ApiKeyLimit:
    limit_type = LimitType(submitted.limit_type)
    window = LimitWindow(submitted.limit_window)
    reset_at = next_limit_reset(now, window)
    since = now - limit_window_delta(window)
    current_value = await repository.get_limit_usage_value(
        key_id, limit_type=limit_type, since=since, until=now, model_filter=submitted.model_filter
    )
    return _limit_input_to_row(submitted, key_id, now, current_value=current_value, reset_at=reset_at)


def _validate_unique_limit_rule_identities(limits: list[LimitRuleInput]) -> None:
    identities: set[tuple[str, str, str | None]] = set()
    for limit in limits:
        identity = _limit_identity_from_input(limit)
        if identity in identities:
            raise ApiKeyValidationError(f"Duplicate limit rules are not allowed: {identity!r}")
        identities.add(identity)


def _normalize_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ApiKeyValidationError("API key name is required")
    return normalized


def _generate_plain_key() -> str:
    return f"sk-clb-{secrets.token_urlsafe(32)}"


def _hash_key(plain_key: str) -> str:
    return sha256(plain_key.encode("utf-8")).hexdigest()


def _serialize_allowed_models(allowed_models: list[str] | None) -> str | None:
    if allowed_models is None:
        return None
    return json.dumps(allowed_models)


def _deserialize_allowed_models(payload: str | None) -> list[str] | None:
    if payload is None:
        return None
    parsed = json.loads(payload)
    if not isinstance(parsed, list):
        return None
    models = [value.strip() for value in parsed if isinstance(value, str) and value.strip()]
    return models


def _normalize_allowed_models(allowed_models: list[str] | None) -> list[str] | None:
    if allowed_models is None:
        return None
    return [model.strip() for model in allowed_models if model and model.strip()]


def _normalize_assigned_account_ids(account_ids: list[str] | None) -> list[str]:
    if not account_ids:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for account_id in account_ids:
        value = account_id.strip()
        if not value or value in seen:
            continue
        normalized.append(value)
        seen.add(value)
    return normalized


def _normalize_model_slug(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


def _normalize_expires_at(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return to_utc_naive(value)


def _normalize_reasoning_effort(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized not in _SUPPORTED_REASONING_EFFORTS:
        options = ", ".join(sorted(_SUPPORTED_REASONING_EFFORTS))
        raise ApiKeyValidationError(f"Unsupported enforced reasoning effort '{normalized}'. Expected one of: {options}")
    return normalized


def _normalize_reasoning_effort_lenient(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized in _SUPPORTED_REASONING_EFFORTS:
        return normalized
    return None


def _normalize_service_tier(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized == "fast":
        normalized = "priority"
    if normalized not in _SUPPORTED_SERVICE_TIERS:
        options = ", ".join(sorted(_SUPPORTED_SERVICE_TIERS | {"fast"}))
        raise ApiKeyValidationError(f"Unsupported enforced service tier '{normalized}'. Expected one of: {options}")
    return normalized


def _normalize_service_tier_lenient(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized == "fast":
        return "priority"
    if normalized in _SUPPORTED_SERVICE_TIERS:
        return normalized
    return None


def _normalize_traffic_class(value: str | None) -> str:
    normalized = (value or TRAFFIC_CLASS_FOREGROUND).strip().lower()
    if normalized not in _SUPPORTED_TRAFFIC_CLASSES:
        options = ", ".join(sorted(_SUPPORTED_TRAFFIC_CLASSES))
        raise ValueError(f"Unsupported traffic class '{normalized}'. Expected one of: {options}")
    return normalized


def _normalize_traffic_class_lenient(value: str | None) -> str:
    normalized = (value or TRAFFIC_CLASS_FOREGROUND).strip().lower()
    if normalized in _SUPPORTED_TRAFFIC_CLASSES:
        return normalized
    return TRAFFIC_CLASS_FOREGROUND


def _validate_model_enforcement(*, enforced_model: str | None, allowed_models: list[str] | None) -> None:
    if enforced_model is None or not allowed_models:
        return
    if enforced_model not in allowed_models:
        raise ApiKeyValidationError(
            "enforced_model must be present in allowed_models when allowed_models is configured"
        )


def _to_limit_rule_data(limit: ApiKeyLimit) -> LimitRuleData:
    return LimitRuleData(
        id=limit.id,
        limit_type=limit.limit_type.value,
        limit_window=limit.limit_window.value,
        max_value=limit.max_value,
        current_value=limit.current_value,
        model_filter=limit.model_filter,
        reset_at=limit.reset_at,
    )


def _ensure_valid_api_key_row(row: ApiKey | None) -> ApiKey:
    if row is None or not row.is_active:
        raise ApiKeyInvalidError("Invalid API key")
    return row


async def _lazy_reset_expired_limits(
    repository: ApiKeysRepositoryProtocol, limits: list[ApiKeyLimit], *, now: datetime
) -> bool:
    reset_performed = False
    for limit in limits:
        if limit.reset_at >= now:
            continue
        new_reset_at = advance_limit_reset(limit.reset_at, now, limit.limit_window)
        await repository.reset_limit(limit.id, expected_reset_at=limit.reset_at, new_reset_at=new_reset_at)
        reset_performed = True
    return reset_performed


def _to_created_data(data: ApiKeyData, key: str) -> ApiKeyCreatedData:
    return ApiKeyCreatedData(
        id=data.id,
        name=data.name,
        key_prefix=data.key_prefix,
        allowed_models=data.allowed_models,
        apply_to_codex_model=data.apply_to_codex_model,
        enforced_model=data.enforced_model,
        enforced_reasoning_effort=data.enforced_reasoning_effort,
        enforced_service_tier=data.enforced_service_tier,
        traffic_class=data.traffic_class,
        expires_at=data.expires_at,
        is_active=data.is_active,
        created_at=data.created_at,
        last_used_at=data.last_used_at,
        limits=data.limits,
        usage_summary=data.usage_summary,
        account_assignment_scope_enabled=data.account_assignment_scope_enabled,
        assigned_account_ids=data.assigned_account_ids,
        member_id=data.member_id,
        key=key,
        allowed_reasoning_efforts=data.allowed_reasoning_efforts,
        transport_policy_override=data.transport_policy_override,
        usage_sections=data.usage_sections,
        source_assignment_scope_enabled=data.source_assignment_scope_enabled,
        assigned_source_ids=data.assigned_source_ids,
    )


def _to_api_key_data(
    row: ApiKey, *, usage_summary: ApiKeyUsageSummaryData | None = None, pooled_credits: PooledCreditData | None = None
) -> ApiKeyData:
    limits = [_to_limit_rule_data(limit) for limit in row.limits] if row.limits else []
    account_assignments = getattr(row, "account_assignments", [])
    source_assignments = getattr(row, "source_assignments", [])
    return ApiKeyData(
        id=row.id,
        name=row.name,
        key_prefix=row.key_prefix,
        allowed_models=_deserialize_allowed_models(row.allowed_models),
        apply_to_codex_model=getattr(row, "apply_to_codex_model", False),
        enforced_model=_normalize_model_slug(row.enforced_model),
        enforced_reasoning_effort=_normalize_reasoning_effort_lenient(row.enforced_reasoning_effort),
        enforced_service_tier=_normalize_service_tier_lenient(row.enforced_service_tier),
        traffic_class=_normalize_traffic_class_lenient(getattr(row, "traffic_class", TRAFFIC_CLASS_FOREGROUND)),
        expires_at=row.expires_at,
        is_active=row.is_active,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        limits=limits,
        usage_summary=usage_summary,
        account_assignment_scope_enabled=getattr(row, "account_assignment_scope_enabled", False),
        assigned_account_ids=[assignment.account_id for assignment in account_assignments],
        pooled_credits=pooled_credits,
        member_id=getattr(row, "member_id", None),
        allowed_reasoning_efforts=_deserialize_allowed_reasoning_efforts(
            getattr(row, "allowed_reasoning_efforts", None)
        ),
        transport_policy_override=_normalize_transport_policy_override_lenient(
            getattr(row, "transport_policy_override", None)
        ),
        usage_sections=_get_usage_sections_with_default(row),
        source_assignment_scope_enabled=getattr(row, "source_assignment_scope_enabled", False),
        assigned_source_ids=[assignment.source_id for assignment in source_assignments],
    )


def _to_usage_summary_data(summary: ApiKeyUsageSummary | None) -> ApiKeyUsageSummaryData | None:
    if summary is None:
        return None
    return ApiKeyUsageSummaryData(
        request_count=summary.request_count,
        total_tokens=summary.total_tokens,
        cached_input_tokens=summary.cached_input_tokens,
        total_cost_usd=summary.total_cost_usd,
    )


def _limit_input_to_row(
    li: LimitRuleInput, key_id: str, now: datetime, *, current_value: int = 0, reset_at: datetime | None = None
) -> ApiKeyLimit:
    window = LimitWindow(li.limit_window)
    if li.limit_type == LimitType.CREDITS.value and li.model_filter is not None:
        raise ApiKeyValidationError("credits limits do not support model_filter")
    return ApiKeyLimit(
        api_key_id=key_id,
        limit_type=LimitType(li.limit_type),
        limit_window=window,
        max_value=li.max_value,
        current_value=current_value,
        model_filter=li.model_filter,
        reset_at=reset_at if reset_at is not None else next_limit_reset(now, window),
    )


async def _build_limit_rows_for_update(
    *,
    key_id: str,
    now: datetime,
    submitted_limits: list[LimitRuleInput],
    existing_limits: list[ApiKeyLimit],
    reset_usage: bool,
    repository: ApiKeysRepositoryProtocol | None = None,
) -> list[ApiKeyLimit]:
    existing_by_key = {_limit_identity_from_row(limit): limit for limit in existing_limits}
    _validate_unique_limit_rule_identities(submitted_limits)
    rows: list[ApiKeyLimit] = []
    for submitted in submitted_limits:
        identity = _limit_identity_from_input(submitted)
        matched = existing_by_key.get(identity)
        if reset_usage:
            rows.append(_limit_input_to_row(submitted, key_id, now))
            continue
        if matched is None:
            if repository is None:
                raise TypeError("repository is required to backfill new API key limit usage")
            rows.append(await _new_limit_input_to_backfilled_row(submitted, key_id, now, repository))
            continue
        rows.append(
            _limit_input_to_row(submitted, key_id, now, current_value=matched.current_value, reset_at=matched.reset_at)
        )
    return rows


def _build_reset_limit_rows(*, key_id: str, now: datetime, existing_limits: list[ApiKeyLimit]) -> list[ApiKeyLimit]:
    rows: list[ApiKeyLimit] = []
    for existing in existing_limits:
        rows.append(
            ApiKeyLimit(
                api_key_id=key_id,
                limit_type=existing.limit_type,
                limit_window=existing.limit_window,
                max_value=existing.max_value,
                current_value=0,
                model_filter=existing.model_filter,
                reset_at=next_limit_reset(now, existing.limit_window),
            )
        )
    return rows


def _limit_identity_from_input(limit: LimitRuleInput) -> tuple[str, str, str | None]:
    return (limit.limit_type, limit.limit_window, limit.model_filter)


def _limit_identity_from_row(limit: ApiKeyLimit) -> tuple[str, str, str | None]:
    return (limit.limit_type.value, limit.limit_window.value, limit.model_filter)
