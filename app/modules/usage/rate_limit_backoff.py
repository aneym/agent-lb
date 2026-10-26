from __future__ import annotations

import random
import time

from app.core.config.settings import get_settings

usage_refresh_auth_cooldowns: dict[str, float] = {}

usage_refresh_rate_limit_strikes: dict[str, int] = {}

_last_rate_limit_at: dict[str, float] = {}

_BASE_SECONDS = 60.0
_MAX_BACKOFF_SECONDS = 900.0
_MAX_RETRY_AFTER_SECONDS = 3600.0


def _backoff_seconds(strikes: int) -> float:
    return min(
        _MAX_BACKOFF_SECONDS,
        _BASE_SECONDS * (2 ** min(strikes - 1, 10)) * random.uniform(0.8, 1.2),
    )


def mark_usage_refresh_auth_cooldown(
    account_id: str,
    status_code: int,
    retry_after_seconds: float | None = None,
) -> None:
    now = time.monotonic()
    if status_code == 429:
        strikes = usage_refresh_rate_limit_strikes.get(account_id, 0) + 1
        usage_refresh_rate_limit_strikes[account_id] = strikes
        _last_rate_limit_at[account_id] = now
        delay = _backoff_seconds(strikes)
        if retry_after_seconds is not None and retry_after_seconds > 0:
            delay = max(delay, min(retry_after_seconds, _MAX_RETRY_AFTER_SECONDS))
        usage_refresh_auth_cooldowns[account_id] = max(usage_refresh_auth_cooldowns.get(account_id, 0.0), now + delay)
        return
    if status_code not in {401, 403}:
        return
    cooldown_seconds = max(0.0, float(get_settings().usage_refresh_auth_failure_cooldown_seconds))
    if cooldown_seconds <= 0:
        return
    usage_refresh_auth_cooldowns[account_id] = now + cooldown_seconds


def is_usage_refresh_in_cooldown(account_id: str) -> bool:
    expires_at = usage_refresh_auth_cooldowns.get(account_id)
    if expires_at is None:
        return False
    if expires_at > time.monotonic():
        return True
    usage_refresh_auth_cooldowns.pop(account_id, None)
    return False


def record_usage_refresh_success(account_id: str) -> None:
    strikes = usage_refresh_rate_limit_strikes.get(account_id, 0)
    if strikes <= 0:
        usage_refresh_auth_cooldowns.pop(account_id, None)
        usage_refresh_rate_limit_strikes.pop(account_id, None)
        _last_rate_limit_at.pop(account_id, None)
        return
    now = time.monotonic()
    if now - _last_rate_limit_at.get(account_id, 0.0) >= _MAX_BACKOFF_SECONDS:
        strikes -= 1
    if strikes <= 0:
        usage_refresh_auth_cooldowns.pop(account_id, None)
        usage_refresh_rate_limit_strikes.pop(account_id, None)
        _last_rate_limit_at.pop(account_id, None)
        return
    usage_refresh_rate_limit_strikes[account_id] = strikes
    usage_refresh_auth_cooldowns[account_id] = now + _backoff_seconds(strikes)


def prune_usage_refresh_auth_cooldowns() -> None:
    now = time.monotonic()
    stale = [account_id for account_id, expires_at in usage_refresh_auth_cooldowns.items() if expires_at <= now]
    for account_id in stale:
        usage_refresh_auth_cooldowns.pop(account_id, None)
