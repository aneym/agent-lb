from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError

from app.modules.api_keys.policy_validation import _SUPPORTED_REASONING_EFFORTS
from app.modules.api_keys.service_data import ApiKeyValidationError


def _limit_identity_from_input(limit: LimitRuleInput) -> tuple[str, str, str | None]:
    return (limit.limit_type, limit.limit_window, limit.model_filter)


@dataclass(frozen=True, slots=True)
class LimitRuleInput:
    limit_type: str
    limit_window: str
    max_value: int
    model_filter: str | None = None


def _normalize_reasoning_effort_lenient(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized in _SUPPORTED_REASONING_EFFORTS:
        return normalized
    return None


_REASONING_POLICY_EXCLUSIVE_CONSTRAINT = "ck_api_keys_reasoning_policy_exclusive"


def _is_reasoning_policy_constraint_error(exc: IntegrityError) -> bool:
    return _REASONING_POLICY_EXCLUSIVE_CONSTRAINT in str(exc).lower()


def _normalize_assigned_source_ids(source_ids: list[str] | None) -> list[str]:
    if not source_ids:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for source_id in source_ids:
        value = source_id.strip()
        if not value or value in seen:
            continue
        normalized.append(value)
        seen.add(value)
    return normalized


def _validate_unique_limit_rule_identities(limits: list[LimitRuleInput]) -> None:
    identities: set[tuple[str, str, str | None]] = set()
    for limit in limits:
        identity = _limit_identity_from_input(limit)
        if identity in identities:
            raise ApiKeyValidationError(f"Duplicate limit rules are not allowed: {identity!r}")
        identities.add(identity)
