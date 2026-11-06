from __future__ import annotations

import time
from collections.abc import Mapping

import aiohttp

from app.core.anthropic.quota_headers import exhausted_account_reset
from app.core.anthropic.quota_headers import parse_reset_epoch as _parse_reset_epoch
from app.core.balancer.types import UpstreamError

_ANTHROPIC_DEFAULT_COOLDOWN_SECONDS = 60


def rate_limit_error_from_response(resp: aiohttp.ClientResponse, message: str) -> UpstreamError:
    error = UpstreamError(message=message)
    quota_reset = exhausted_account_reset(resp.headers)
    if quota_reset is not None:
        error["quota_reset_at"] = quota_reset
    reset_at = quota_reset or anthropic_rate_limit_reset_at(resp.headers)
    if reset_at is not None:
        error["resets_at"] = reset_at
    retry_after = retry_after_seconds(resp.headers)
    if retry_after is not None:
        error["resets_in_seconds"] = retry_after
    return error


def reset_at_from_rate_limit_error(error: UpstreamError) -> int:
    reset_at = error.get("resets_at")
    if isinstance(reset_at, int | float):
        return int(reset_at)
    reset_in_seconds = error.get("resets_in_seconds")
    if isinstance(reset_in_seconds, int | float):
        return int(time.time() + max(0, reset_in_seconds))
    return int(time.time() + _ANTHROPIC_DEFAULT_COOLDOWN_SECONDS)


def anthropic_rate_limit_reset_at(headers: Mapping[str, str]) -> int | None:
    reset_candidates = [
        "anthropic-ratelimit-unified-reset",
        "anthropic-ratelimit-unified-overage-reset",
        "anthropic-ratelimit-requests-reset",
        "anthropic-ratelimit-tokens-reset",
        "anthropic-ratelimit-input-tokens-reset",
        "anthropic-ratelimit-output-tokens-reset",
        "anthropic-priority-input-tokens-reset",
        "anthropic-priority-output-tokens-reset",
        "anthropic-fast-input-tokens-reset",
        "anthropic-fast-output-tokens-reset",
    ]
    epochs = [_parse_reset_epoch(get_header(headers, name)) for name in reset_candidates]
    future_epochs = [epoch for epoch in epochs if epoch is not None and epoch > int(time.time())]
    if future_epochs:
        return min(future_epochs)
    past_epochs = [epoch for epoch in epochs if epoch is not None]
    return max(past_epochs) if past_epochs else None


def retry_after_seconds(headers: Mapping[str, str]) -> int | None:
    retry_after = get_header(headers, "retry-after")
    if retry_after is None:
        return None
    try:
        return max(0, int(float(retry_after)))
    except ValueError:
        return None


def get_header(headers: Mapping[str, str], name: str) -> str | None:
    if hasattr(headers, "get"):
        value = headers.get(name)
        if value is not None:
            return value
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None
