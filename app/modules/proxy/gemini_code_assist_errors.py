from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any

from app.modules.proxy.gemini_upstream import GeminiFailureKind, GeminiUpstreamError

MAX_RATE_LIMIT_DELAY_SECONDS = 300.0
MESSAGE_LIMIT = 500
_ERROR_INFO = "type.googleapis.com/google.rpc.ErrorInfo"
_RETRY_INFO = "type.googleapis.com/google.rpc.RetryInfo"
_QUOTA_FAILURE = "type.googleapis.com/google.rpc.QuotaFailure"
_DURATION_PART = re.compile(r"([0-9]+(?:\.[0-9]+)?)(ms|h|m|s)")
_CAPACITY = re.compile(r"exhausted your capacity|capacity exceeded|MODEL_CAPACITY_EXHAUSTED", re.IGNORECASE)
_CAPACITY_REASONS = frozenset({"MODEL_CAPACITY_EXHAUSTED", "MODEL_CAPACITY_EXCEEDED"})
_TERMS_REASONS = frozenset({"TOS_VIOLATION", "ACCOUNT_DISABLED"})
_VALIDATION_REASON = "VALIDATION_REQUIRED"
_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}


def duration_seconds(value: object) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    parts = _DURATION_PART.findall(text)
    if not parts or "".join(number + unit for number, unit in parts) != text:
        return None
    return sum(float(number) * _UNIT_SECONDS[unit] for number, unit in parts)


def _timestamp(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)).timestamp()


def _error_object(raw: bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        return {}
    if isinstance(data, list) and data and isinstance(data[0], dict):
        data = data[0]
    error = data.get("error") if isinstance(data, dict) else None
    return error if isinstance(error, dict) else {}


def _details(error: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    details = error.get("details")
    if not isinstance(details, list):
        return []
    return [detail for detail in details if isinstance(detail, dict) and detail.get("@type") == kind]


def _reset_at(error: dict[str, Any], now: float) -> float | None:
    for info in _details(error, _ERROR_INFO):
        metadata = info.get("metadata")
        if not isinstance(metadata, dict):
            continue
        stamp = _timestamp(metadata.get("quotaResetTimeStamp"))
        if stamp is not None and stamp > now:
            return stamp
        delay = duration_seconds(metadata.get("quotaResetDelay"))
        if delay is not None and delay > 0:
            return now + delay
    for info in _details(error, _RETRY_INFO):
        delay = duration_seconds(info.get("retryDelay"))
        if delay is not None and delay > 0:
            return now + delay
    return None


def _validation_url(error: dict[str, Any]) -> str | None:
    for info in _details(error, _ERROR_INFO):
        metadata = info.get("metadata")
        if not isinstance(metadata, dict):
            continue
        for key in ("validation_url", "validationUrl"):
            url = metadata.get(key)
            if isinstance(url, str) and url.startswith("https://"):
                return url
    return None


def _daily_quota(error: dict[str, Any]) -> bool:
    for failure in _details(error, _QUOTA_FAILURE):
        violations = failure.get("violations")
        for violation in violations if isinstance(violations, list) else []:
            quota_id = violation.get("quotaId") if isinstance(violation, dict) else None
            if isinstance(quota_id, str) and ("PerDay" in quota_id or "Daily" in quota_id):
                return True
    return False


def _kind(status: int, error: dict[str, Any], message: str, reset_at: float | None, now: float) -> GeminiFailureKind:
    reasons = {info.get("reason") for info in _details(error, _ERROR_INFO)}
    if status == 401:
        return "auth"
    if status == 403:
        if _VALIDATION_REASON in reasons:
            return "validation_required"
        terms = reasons & _TERMS_REASONS or "terms of service" in message.lower()
        return "terms_disabled" if terms else "invalid_request"
    if status == 429:
        if reasons & _CAPACITY_REASONS or _CAPACITY.search(message):
            return "transient"
        long_wait = reset_at is not None and reset_at - now > MAX_RATE_LIMIT_DELAY_SECONDS
        if "QUOTA_EXHAUSTED" in reasons or _daily_quota(error) or long_wait:
            return "quota"
        return "rate_limit"
    if status in (400, 404, 409, 413):
        return "invalid_request"
    return "transient"


def upstream_error(status: int, raw: bytes) -> GeminiUpstreamError:
    now = time.time()
    error = _error_object(raw)
    message = error.get("message")
    text = message.strip()[:MESSAGE_LIMIT] if isinstance(message, str) and message.strip() else ""
    text = text or f"Gemini Code Assist answered {status}."
    reset_at = _reset_at(error, now) if status == 429 else None
    kind = _kind(status, error, text, reset_at, now)
    validation_url = _validation_url(error) if kind == "validation_required" else None
    return GeminiUpstreamError(status, text, kind=kind, reset_at=reset_at, validation_url=validation_url)
