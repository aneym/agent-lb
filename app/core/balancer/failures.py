from __future__ import annotations

import time
from typing import Literal

from app.core.balancer.state import (
    DRAIN_ERROR_COUNT_THRESHOLD,
    DRAIN_ERROR_WINDOW_SECONDS,
    DRAIN_PRIMARY_THRESHOLD_PCT,
    DRAIN_SECONDARY_THRESHOLD_PCT,
    HEALTH_TIER_DRAINING,
    HEALTH_TIER_HEALTHY,
    HEALTH_TIER_PROBING,
    PROBE_QUIET_SECONDS,
    PROBE_SUCCESS_STREAK_REQUIRED,
    AccountState,
)
from app.core.balancer.types import FailureClass, UpstreamError
from app.db.models import AccountStatus

PERMANENT_FAILURE_CODES = {
    "refresh_token_expired": "Refresh token expired - re-login required",
    "refresh_token_reused": "Refresh token was reused - re-login required",
    "refresh_token_invalidated": "Refresh token was revoked - re-login required",
    "invalid_grant": "Refresh token grant invalid - re-login required",
    "token_invalidated": "Authentication token invalidated - re-login required",
    "token_expired": "Authentication token expired - re-login required",
    "account_session_expired": "ChatGPT session ended - re-login required",
    "account_auth_invalidated": "Authentication failed after token refresh - re-login required",
    "account_deactivated": "Account has been deactivated",
    "account_suspended": "Account has been suspended",
    "account_deleted": "Account has been deleted",
}
REAUTH_REQUIRED_FAILURE_CODES = frozenset(
    {
        "refresh_token_expired",
        "refresh_token_reused",
        "refresh_token_invalidated",
        "invalid_grant",
        "token_invalidated",
        "token_expired",
        "account_session_expired",
        "account_auth_invalidated",
    }
)
_AUTH_REFRESH_ERROR_PREFIX = "auth_refresh_"


def handle_rate_limit(state: AccountState, error: UpstreamError) -> None:
    now = time.time()
    state.status = AccountStatus.RATE_LIMITED
    state.error_count += 1
    state.last_error_at = now
    state.blocked_at = now
    retry_at = _live_quota_retry_at(error, state.error_count, now=now)
    state.reset_at = retry_at
    state.cooldown_until = retry_at


QUOTA_EXCEEDED_COOLDOWN_SECONDS = 120.0
SELECTOR_RETRY_HINT_MAX_SECONDS = 300


def _format_retry_hint(wait_seconds: float) -> str:
    capped = min(max(0.0, wait_seconds), float(SELECTOR_RETRY_HINT_MAX_SECONDS))
    return f"Rate limit exceeded. Try again in {capped:.0f}s"


def handle_quota_exceeded(state: AccountState, error: UpstreamError) -> None:
    now = time.time()
    state.status = AccountStatus.QUOTA_EXCEEDED
    state.error_count += 1
    state.last_error_at = now
    state.used_percent = 100.0
    state.raw_used_percent = 100.0
    state.blocked_at = now
    retry_at = _live_quota_retry_at(error, state.error_count, now=now)
    state.reset_at = retry_at
    state.cooldown_until = retry_at


def _live_quota_retry_at(error: UpstreamError, failures: int, *, now: float) -> float:

    delays = (60, 300, 900, 3600)
    delay = delays[min(max(failures, 1) - 1, len(delays) - 1)]
    provider_reset = _extract_reset_at(error)
    if provider_reset is not None and provider_reset > now:
        delay = min(delay, provider_reset - now)
    return now + delay


def canonical_permanent_failure_code(error_code: str) -> str:
    if error_code.startswith(_AUTH_REFRESH_ERROR_PREFIX):
        candidate = error_code.removeprefix(_AUTH_REFRESH_ERROR_PREFIX)
        if candidate in PERMANENT_FAILURE_CODES:
            return candidate
    return error_code


def permanent_failure_reason(error_code: str) -> str:
    canonical_code = canonical_permanent_failure_code(error_code)
    return PERMANENT_FAILURE_CODES.get(
        canonical_code,
        f"Authentication failed: {error_code}",
    )


def handle_permanent_failure(state: AccountState, error_code: str) -> None:
    state.status = account_status_for_permanent_failure(error_code)
    state.deactivation_reason = permanent_failure_reason(error_code)
    state.blocked_at = None


def account_status_for_permanent_failure(error_code: str) -> AccountStatus:
    canonical_code = canonical_permanent_failure_code(error_code)
    if canonical_code in REAUTH_REQUIRED_FAILURE_CODES:
        return AccountStatus.REAUTH_REQUIRED
    return AccountStatus.DEACTIVATED


FailoverAction = Literal["failover_next", "surface"]


def failover_decision(
    *,
    failure_class: FailureClass,
    downstream_visible: bool,
    candidates_remaining: int,
) -> FailoverAction:
    if downstream_visible:
        return "surface"
    if candidates_remaining <= 0:
        return "surface"
    if failure_class in ("rate_limit", "quota", "retryable_transient"):
        return "failover_next"
    return "surface"


def _extract_reset_at(error: UpstreamError) -> int | None:
    reset_at = error.get("resets_at")
    if reset_at is not None:
        return int(reset_at)
    reset_in = error.get("resets_in_seconds")
    if reset_in is not None:
        return int(time.time() + float(reset_in))
    return None


def evaluate_health_tier(
    state: AccountState,
    *,
    now: float | None = None,
    drain_entered_at: float | None = None,
    probe_success_streak: int = 0,
    drain_primary_threshold_pct: float = DRAIN_PRIMARY_THRESHOLD_PCT,
    drain_secondary_threshold_pct: float = DRAIN_SECONDARY_THRESHOLD_PCT,
    drain_error_window_seconds: float = DRAIN_ERROR_WINDOW_SECONDS,
    drain_error_count_threshold: int = DRAIN_ERROR_COUNT_THRESHOLD,
    probe_quiet_seconds: float = PROBE_QUIET_SECONDS,
    probe_success_streak_required: int = PROBE_SUCCESS_STREAK_REQUIRED,
) -> int:
    current = now or time.time()

    if state.status in (
        AccountStatus.RATE_LIMITED,
        AccountStatus.QUOTA_EXCEEDED,
        AccountStatus.PAUSED,
        AccountStatus.REAUTH_REQUIRED,
        AccountStatus.DEACTIVATED,
    ):
        return state.health_tier

    should_drain = False

    if state.used_percent is not None and state.used_percent >= drain_primary_threshold_pct:
        should_drain = True

    if state.secondary_used_percent is not None and state.secondary_used_percent >= drain_secondary_threshold_pct:
        should_drain = True

    if (
        state.error_count >= drain_error_count_threshold
        and state.last_error_at is not None
        and current - state.last_error_at < drain_error_window_seconds
    ):
        should_drain = True

    current_tier = state.health_tier

    if current_tier == HEALTH_TIER_HEALTHY:
        return HEALTH_TIER_DRAINING if should_drain else HEALTH_TIER_HEALTHY

    if current_tier == HEALTH_TIER_DRAINING:
        if should_drain:
            return HEALTH_TIER_DRAINING
        if drain_entered_at is not None and current - drain_entered_at >= probe_quiet_seconds:
            return HEALTH_TIER_PROBING
        return HEALTH_TIER_DRAINING

    if current_tier == HEALTH_TIER_PROBING:
        if should_drain:
            return HEALTH_TIER_DRAINING
        if probe_success_streak >= probe_success_streak_required:
            return HEALTH_TIER_HEALTHY
        return HEALTH_TIER_PROBING

    return HEALTH_TIER_HEALTHY
