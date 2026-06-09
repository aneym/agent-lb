from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

import aiohttp
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.clients.http import lease_retry_client
from app.core.clients.usage import UsageFetchError, _extract_error_code, _extract_error_message, _retry_options
from app.core.config.settings import get_settings
from app.core.usage.models import RateLimitPayload, UsagePayload, UsageWindow
from app.core.utils.request_id import get_request_id

logger = logging.getLogger(__name__)

_FIVE_HOUR_SECONDS = 5 * 60 * 60
_SEVEN_DAY_SECONDS = 7 * 24 * 60 * 60


class AnthropicUsageWindow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    utilization: float | None = None
    resets_at: str | None = None


class AnthropicOAuthUsagePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    five_hour: AnthropicUsageWindow | None = None
    seven_day: AnthropicUsageWindow | None = None


async def fetch_anthropic_usage(
    *,
    access_token: str,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    client: aiohttp.ClientSession | None = None,
) -> UsagePayload:
    settings = get_settings()
    usage_base = base_url or settings.anthropic_upstream_base_url
    url = urljoin(usage_base.rstrip("/") + "/", "api/oauth/usage")
    timeout = aiohttp.ClientTimeout(total=timeout_seconds or settings.usage_fetch_timeout_seconds)
    retries = max_retries if max_retries is not None else settings.usage_fetch_max_retries
    headers = _anthropic_usage_headers(access_token)
    retry_options = _retry_options(retries + 1)

    try:
        if client is not None:
            async with client.get(url, headers=headers, timeout=timeout) as resp:
                return await _usage_payload_or_raise(resp)
        async with lease_retry_client() as retry_client:
            async with retry_client.request(
                "GET",
                url,
                headers=headers,
                timeout=timeout,
                retry_options=retry_options,
            ) as resp:
                return await _usage_payload_or_raise(resp)
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.warning("Anthropic usage fetch error request_id=%s error=%s", get_request_id(), exc)
        raise UsageFetchError(0, f"Anthropic usage fetch failed: {exc}") from exc


def _usage_payload_from_anthropic(payload: AnthropicOAuthUsagePayload) -> UsagePayload:
    return UsagePayload(
        plan_type="claude",
        rate_limit=RateLimitPayload(
            primary_window=_usage_window(payload.five_hour, limit_window_seconds=_FIVE_HOUR_SECONDS),
            secondary_window=_usage_window(payload.seven_day, limit_window_seconds=_SEVEN_DAY_SECONDS),
        ),
    )


def _usage_window(
    window: AnthropicUsageWindow | None,
    *,
    limit_window_seconds: int,
) -> UsageWindow | None:
    if window is None or window.utilization is None:
        return None
    return UsageWindow(
        used_percent=float(window.utilization),
        reset_at=_parse_rfc3339_epoch(window.resets_at),
        limit_window_seconds=limit_window_seconds,
    )


def _parse_rfc3339_epoch(value: str | None) -> int | None:
    if not value:
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        return int(datetime.fromisoformat(normalized).timestamp())
    except ValueError:
        return None


def _anthropic_usage_headers(access_token: str) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "User-Agent": "claude-cli/2.1.170",
    }
    request_id = get_request_id()
    if request_id:
        headers["x-request-id"] = request_id
    return headers


async def _safe_json(resp: aiohttp.ClientResponse) -> dict[str, Any]:
    try:
        data = await resp.json(content_type=None)
    except Exception:
        text = await resp.text()
        return {"error": {"message": text.strip()}}
    return data if isinstance(data, dict) else {"error": {"message": str(data)}}


async def _usage_payload_or_raise(resp: aiohttp.ClientResponse) -> UsagePayload:
    data = await _safe_json(resp)
    if resp.status >= 400:
        code = _extract_error_code(data)
        message = _extract_error_message(data) or f"Anthropic usage fetch failed ({resp.status})"
        logger.warning(
            "Anthropic usage fetch failed request_id=%s status=%s code=%s message=%s",
            get_request_id(),
            resp.status,
            code,
            message,
        )
        raise UsageFetchError(resp.status, message, code=code)
    try:
        payload = AnthropicOAuthUsagePayload.model_validate(data)
    except ValidationError as exc:
        logger.warning("Anthropic usage fetch invalid payload request_id=%s", get_request_id())
        raise UsageFetchError(502, "Invalid Anthropic usage payload") from exc
    return _usage_payload_from_anthropic(payload)
