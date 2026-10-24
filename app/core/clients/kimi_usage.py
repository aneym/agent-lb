from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from typing import Any

import aiohttp

from app.core.anthropic.quota_headers import parse_reset_epoch
from app.core.clients.http import lease_http_session
from app.core.clients.usage import UsageFetchError
from app.core.config.settings import get_settings
from app.core.usage.models import AdditionalRateLimitPayload, RateLimitPayload, UsagePayload, UsageWindow


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) and 0 <= parsed <= 1e12 else None


def _reset(value: Any) -> int | None:
    return parse_reset_epoch(value) if isinstance(value, str) else None


def _ratio_window(raw: Any, seconds: int) -> UsageWindow | None:
    if not isinstance(raw, Mapping):
        return None
    ratio = _number(raw.get("used_ratio"))
    if ratio is None or ratio > 1:
        return None
    return UsageWindow(used_percent=ratio * 100, reset_at=_reset(raw.get("reset_time")), limit_window_seconds=seconds)


def _usage_payload(data: dict[str, Any]) -> UsagePayload:
    usages = data.get("usages")
    usages = usages if isinstance(usages, dict) else {}
    primary = _ratio_window(usages.get("limit_5h"), 18000)
    secondary = None
    monthly_total = _ratio_window(usages.get("limit_month_total"), 2592000)
    monthly_code = _ratio_window(usages.get("limit_month_code"), 2592000)
    limits = data.get("limits")
    for raw in limits if isinstance(limits, list) else []:
        if not isinstance(raw, dict):
            continue
        duration = raw.get("window")
        detail = raw.get("detail", raw)
        if not isinstance(duration, dict) or not isinstance(detail, dict):
            continue
        units = {"TIME_UNIT_MINUTE": 60, "TIME_UNIT_HOUR": 3600, "TIME_UNIT_DAY": 86400}
        length = _number(duration.get("duration"))
        unit = duration.get("timeUnit")
        seconds = (
            int(length * units.get(unit, 0)) if length is not None and length <= 10080 and isinstance(unit, str) else 0
        )
        limit, used = _number(detail.get("limit")), _number(detail.get("used"))
        if used is None and limit is not None:
            remaining = _number(detail.get("remaining"))
            used = max(0, limit - remaining) if remaining is not None else None
        if not limit or used is None:
            continue
        window = UsageWindow(
            used_percent=used / limit * 100,
            reset_at=_reset(detail.get("resetTime")),
            limit_window_seconds=seconds,
        )
        if seconds == 18000:
            primary = window
        elif seconds == 604800:
            secondary = window
    weekly = data.get("usage")
    if secondary is None and isinstance(weekly, dict):
        limit, used = _number(weekly.get("limit")), _number(weekly.get("used"))
        if limit and used is not None:
            secondary = UsageWindow(
                used_percent=used / limit * 100,
                reset_at=_reset(weekly.get("resetTime")),
                limit_window_seconds=604800,
            )
    month_windows = [window for window in (monthly_total, monthly_code) if window is not None]
    monthly = max(month_windows, key=lambda window: window.used_percent or 0) if month_windows else None
    exhausted = [window for window in month_windows if (window.used_percent or 0) >= 100]
    if monthly is not None and exhausted:
        monthly = monthly.model_copy(
            update={
                "reset_at": max((window.reset_at for window in exhausted if window.reset_at is not None), default=None)
            }
        )
    additional = [
        AdditionalRateLimitPayload(
            limit_name=name, metered_feature=name, rate_limit=RateLimitPayload(primary_window=window)
        )
        for name, window in (("kimi_month_total", monthly_total), ("kimi_month_code", monthly_code))
        if window is not None
    ]
    return UsagePayload(
        plan_type="kimi-coding",
        rate_limit=RateLimitPayload(primary_window=primary, secondary_window=secondary, monthly_window=monthly),
        additional_rate_limits=additional or None,
    )


async def fetch_kimi_usage(*, access_token: str) -> UsagePayload:
    settings = get_settings()
    url = settings.kimi_anthropic_upstream_base_url.rstrip("/") + "/v1/usages"
    try:
        async with lease_http_session() as session:
            async with session.get(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=aiohttp.ClientTimeout(total=settings.usage_fetch_timeout_seconds),
            ) as resp:
                if resp.status >= 400:
                    raise UsageFetchError(resp.status, f"Kimi quota fetch failed ({resp.status})")
                data = await resp.json(content_type=None)
                if not isinstance(data, dict):
                    raise UsageFetchError(502, "Invalid Kimi quota payload")
                return _usage_payload(data)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        raise UsageFetchError(502, "Kimi quota fetch unavailable") from exc
