from __future__ import annotations

import logging
import time
from collections.abc import Mapping

import aiohttp

from app.core.anthropic.quota_headers import exhausted_account_reset
from app.core.anthropic.quota_headers import parse_reset_epoch as _parse_reset_epoch
from app.core.balancer.types import UpstreamError
from app.core.utils.time import naive_utc_to_epoch
from app.db.models import Account, AccountStatus, AdditionalUsageHistory, UsageHistory
from app.modules.proxy import limit_backoff
from app.modules.proxy.repo_bundle import ProxyRepoFactory

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


KIMI_MONTHLY_LIMIT_REASON = "Kimi monthly usage limit for this billing cycle"


async def kimi_monthly_rate_limit_error(
    account: Account, error: UpstreamError, repo_factory: ProxyRepoFactory
) -> UpstreamError:
    if "monthly usage limit for this billing cycle" not in error.get("message", "").lower():
        return error
    error["monthly_quota"] = True
    reset_at = None
    try:
        async with repo_factory() as repos:
            monthly = await repos.usage.latest_entry_for_account(account.id, window="monthly")
            if monthly is not None:
                reset_at = monthly.reset_at
    except Exception:
        logging.getLogger(__name__).warning("Could not read Kimi monthly reset account_id=%s", account.id)
    if (reset_at is None or reset_at <= time.time()) and account.subscription_current_period_end_at is not None:
        reset_at = int(naive_utc_to_epoch(account.subscription_current_period_end_at))
    if reset_at is not None and reset_at > time.time():
        error["resets_at"] = reset_at
        error["quota_reset_at"] = reset_at
    return error


def bounded_retry_at(reset_at: float | None, blocked_at: float) -> float:
    if reset_at is not None and limit_backoff.enabled():
        return reset_at
    if reset_at is None or int(reset_at) > int(blocked_at) + 3600:
        return blocked_at + 60.0
    return reset_at


def kimi_monthly_selection_state(
    account: Account,
    monthly: UsageHistory | AdditionalUsageHistory | None,
    runtime_reset: float | None,
    cooldown: float | None,
) -> tuple[AccountStatus, float | None, float | None]:
    reset = float(account.reset_at) if account.reset_at else runtime_reset
    if (
        account.blocked_at is not None
        and monthly is not None
        and monthly.window == "monthly"
        and monthly.recorded_at is not None
        and naive_utc_to_epoch(monthly.recorded_at) > account.blocked_at
        and monthly.used_percent is not None
        and monthly.used_percent < 100.0
    ):
        return AccountStatus.ACTIVE, None, None
    if reset is not None and reset <= time.time():
        return AccountStatus.ACTIVE, None, None
    return account.status, reset, cooldown
