from __future__ import annotations

import json

from app.db.models import ApiKey
from app.modules.api_keys.service_data import ApiKeyValidationError

_REASONING_EFFORT_ORDER = ("minimal", "low", "medium", "high", "xhigh", "max", "ultra")
_SUPPORTED_REASONING_EFFORTS = frozenset({"none", *_REASONING_EFFORT_ORDER})
_SUPPORTED_TRANSPORT_POLICY_OVERRIDES = frozenset({"smart", "always_http", "always_websocket"})
_SUPPORTED_SELECTABLE_REASONING_EFFORTS = frozenset(_REASONING_EFFORT_ORDER)
_VALID_USAGE_SECTIONS = {"upstream_limits", "account_pool_usage"}
_DEFAULT_USAGE_SECTIONS = "upstream_limits,account_pool_usage"


def _normalize_allowed_reasoning_efforts(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    normalized: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise ApiKeyValidationError("Allowed reasoning efforts must be strings")
        effort = value.strip().lower()
        if effort not in _SUPPORTED_SELECTABLE_REASONING_EFFORTS:
            options = ", ".join(_REASONING_EFFORT_ORDER)
            raise ApiKeyValidationError(f"Unsupported allowed reasoning effort '{effort}'. Expected one of: {options}")
        normalized.add(effort)
    if not normalized:
        raise ApiKeyValidationError("Allowed reasoning efforts must not be empty")
    return [effort for effort in _REASONING_EFFORT_ORDER if effort in normalized]


def _deserialize_allowed_reasoning_efforts(payload: str | None) -> list[str] | None:
    if payload is None:
        return None
    try:
        parsed = json.loads(payload)
        if not isinstance(parsed, list):
            return []
        return _normalize_allowed_reasoning_efforts(parsed)
    except (ApiKeyValidationError, TypeError, json.JSONDecodeError):
        return []


def _serialize_allowed_reasoning_efforts(allowed_reasoning_efforts: list[str] | None) -> str | None:
    if allowed_reasoning_efforts is None:
        return None
    return json.dumps(allowed_reasoning_efforts)


def _validate_reasoning_effort_policy(
    *, enforced_reasoning_effort: str | None, allowed_reasoning_efforts: list[str] | None
) -> None:
    if enforced_reasoning_effort is not None and allowed_reasoning_efforts is not None:
        raise ApiKeyValidationError(
            "enforced_reasoning_effort and allowed_reasoning_efforts cannot be configured together"
        )


def _normalize_transport_policy_override(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in _SUPPORTED_TRANSPORT_POLICY_OVERRIDES:
        return normalized
    options = ", ".join(sorted(_SUPPORTED_TRANSPORT_POLICY_OVERRIDES))
    raise ApiKeyValidationError(f"Unsupported transport policy override '{normalized}'. Expected one of: {options}")


def _normalize_transport_policy_override_lenient(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in _SUPPORTED_TRANSPORT_POLICY_OVERRIDES:
        return normalized
    return None


def _normalize_usage_sections(raw: str | None) -> str:
    if raw is None:
        return _DEFAULT_USAGE_SECTIONS
    if not raw.strip():
        return ""
    sections = [s.strip() for s in raw.split(",") if s.strip()]
    invalid = [s for s in sections if s not in _VALID_USAGE_SECTIONS]
    if invalid:
        raise ApiKeyValidationError(f"Invalid usage sections: {', '.join(invalid)}")
    return ",".join(dict.fromkeys(sections))


def _get_usage_sections_with_default(row: ApiKey) -> str:
    value = getattr(row, "usage_sections", None)
    if value is None:
        return _DEFAULT_USAGE_SECTIONS
    return value
