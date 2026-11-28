from __future__ import annotations

import logging
from enum import Enum

logger = logging.getLogger(__name__)


class DegradationLevel(Enum):
    NORMAL = "normal"
    DEGRADED = "degraded"
    CRITICAL = "critical"


_level: DegradationLevel = DegradationLevel.NORMAL
_reason: str | None = None
_provider_reasons: dict[str, str] = {}


def _normalize_provider(provider: str | None) -> str | None:
    if provider is None:
        return None
    normalized = provider.strip().lower()
    return normalized or None


def set_degraded(reason: str | None = None, *, provider: str | None = None) -> None:
    global _level, _reason
    provider_key = _normalize_provider(provider)
    effective_reason = reason or "unknown reason"
    if provider_key is not None:
        _provider_reasons[provider_key] = effective_reason
        logger.warning("Operating in degraded mode (%s): %s", provider_key, effective_reason)
        return
    _level = DegradationLevel.DEGRADED
    _reason = reason
    logger.warning("Operating in degraded mode: %s", effective_reason)


def set_normal(*, provider: str | None = None) -> None:
    global _level, _reason
    provider_key = _normalize_provider(provider)
    if provider_key is not None:
        _provider_reasons.pop(provider_key, None)
        return
    _level = DegradationLevel.NORMAL
    _reason = None
    _provider_reasons.clear()


def is_degraded(*, provider: str | None = None) -> bool:
    provider_key = _normalize_provider(provider)
    if provider_key is not None:
        return provider_key in _provider_reasons or _level != DegradationLevel.NORMAL
    return _level != DegradationLevel.NORMAL or bool(_provider_reasons)


def get_status(*, provider: str | None = None) -> dict[str, str | None]:
    provider_key = _normalize_provider(provider)
    if provider_key is not None:
        if provider_key in _provider_reasons:
            return {
                "level": DegradationLevel.DEGRADED.value,
                "reason": _provider_reasons[provider_key],
            }
        if _level != DegradationLevel.NORMAL:
            return {
                "level": _level.value,
                "reason": _reason,
            }
        return {
            "level": DegradationLevel.NORMAL.value,
            "reason": None,
        }
    if _level != DegradationLevel.NORMAL:
        return {
            "level": _level.value,
            "reason": _reason,
        }
    if _provider_reasons:
        combined = "; ".join(f"{name}: {reason}" for name, reason in sorted(_provider_reasons.items()))
        return {
            "level": DegradationLevel.DEGRADED.value,
            "reason": combined,
        }
    return {
        "level": DegradationLevel.NORMAL.value,
        "reason": None,
    }


def format_degraded_error_message(message: str | None, *, provider: str | None = None) -> str:
    degradation_status = get_status(provider=provider)
    reason = degradation_status.get("reason") or "upstream capacity is currently unavailable"
    base_message = message or "Upstream unavailable"
    return f"{base_message}. Service is operating in degraded mode: {reason}"
