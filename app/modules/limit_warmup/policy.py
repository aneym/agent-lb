from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core import usage as usage_core
from app.core.utils.time import utcnow
from app.db.models import (
    Account,
    AccountLimitWarmup,
    AccountStatus,
    AdditionalUsageHistory,
    UsageHistory,
)
from app.modules.accounts.subscription_status import is_subscription_usable
from app.modules.limit_warmup.constants import (
    _ANTHROPIC_CONTINUOUS_WINDOW,
    _ANTHROPIC_PRIME_SPACING_SLACK,
    _ANTHROPIC_RESET_GRACE_SECONDS,
    _ANTHROPIC_TELEMETRY_MAX_AGE_SECONDS,
    _DEFAULT_PRIMARY_WINDOW_MINUTES,
    _DEFAULT_WORKING_HOURS_END,
    _DEFAULT_WORKING_HOURS_START,
    _OPENAI_CONTINUOUS_WINDOW,
    _OPENAI_FAILURE_BRAKE_SECONDS,
    _OPENAI_LOG_ERROR_CODE,
    _OPENAI_PRIME_BUCKET_SECONDS,
    _OPENAI_PRIME_SPACING_SECONDS,
    _OPENAI_TELEMETRY_MAX_AGE_SECONDS,
    _OPENAI_UNSTARTED_LEAD_SLACK_SECONDS,
)
from app.modules.limit_warmup.repository import (
    _MAX_CONTINUOUS_RETRIES,
)
from app.modules.quota_planner.calendar import _parse_hhmm, _to_planner_tz
from app.modules.quota_planner.logic import PlannerSettings
from app.modules.usage.mappers import usage_history_to_window_row


@dataclass(frozen=True, slots=True)
class _WarmupCandidate:
    reset_at: int


def _selected_windows(value: str) -> tuple[str, ...]:
    normalized = value.strip().lower()
    if normalized == "both":
        return ("primary", "secondary")
    if normalized in {"primary", "secondary"}:
        return (normalized,)
    return ()


def _account_is_safe_candidate(account: Account) -> bool:
    return account.status == AccountStatus.ACTIVE and is_subscription_usable(account)


def _in_cooldown(attempt: AccountLimitWarmup | None, *, cooldown_seconds: int) -> bool:
    if attempt is None:
        return False
    return utcnow() - attempt.attempted_at < timedelta(seconds=cooldown_seconds)


def _anthropic_prime_key(
    primary: UsageHistory | None,
    weekly: UsageHistory | None,
    standard: AdditionalUsageHistory | None,
    latest_attempt: AccountLimitWarmup | None,
    now: datetime,
) -> int | None:

    if primary is None or weekly is None:
        return None
    now_epoch = _as_utc(now).timestamp()
    primary_recorded = _as_utc(primary.recorded_at).timestamp()
    if now_epoch - primary_recorded > _ANTHROPIC_TELEMETRY_MAX_AGE_SECONDS:
        return None
    if now_epoch - _as_utc(weekly.recorded_at).timestamp() > _ANTHROPIC_TELEMETRY_MAX_AGE_SECONDS:
        return None
    if primary.used_percent != 0 or weekly.used_percent >= 100:
        return None
    if primary.reset_at is not None and primary_recorded < primary.reset_at + _ANTHROPIC_RESET_GRACE_SECONDS:
        return None
    if standard is not None and standard.used_percent >= 100:
        if standard.reset_at is None or standard.reset_at > now_epoch:
            return None
    window_seconds = (primary.window_minutes or _DEFAULT_PRIMARY_WINDOW_MINUTES) * 60
    if (
        latest_attempt is not None
        and latest_attempt.window == _ANTHROPIC_CONTINUOUS_WINDOW
        and latest_attempt.status != "failed"
    ):
        since_prime = now_epoch - _as_utc(latest_attempt.attempted_at).timestamp()
        if since_prime < window_seconds - _ANTHROPIC_PRIME_SPACING_SLACK:
            return None
    return int(now_epoch // window_seconds) * window_seconds


def _safe_openai_log_error_code(code: str | None) -> str:
    return code if isinstance(code, str) and _OPENAI_LOG_ERROR_CODE.fullmatch(code) else "unrecognized"


def _openai_prime_key(
    primary: UsageHistory | None,
    secondary: UsageHistory | None,
    latest_attempt: AccountLimitWarmup | None,
    now: datetime,
) -> int | None:

    now_epoch = _as_utc(now).timestamp()
    fresh = [
        sample
        for sample in (primary, secondary)
        if sample is not None
        and 0 <= now_epoch - _as_utc(sample.recorded_at).timestamp() <= _OPENAI_TELEMETRY_MAX_AGE_SECONDS
    ]
    if not fresh or any(sample.used_percent >= 100 for sample in fresh):
        return None
    if not any(
        sample.used_percent == 0
        and sample.reset_at is not None
        and sample.window_minutes
        and sample.reset_at - _as_utc(sample.recorded_at).timestamp()
        >= sample.window_minutes * 60 - _OPENAI_UNSTARTED_LEAD_SLACK_SECONDS
        for sample in fresh
    ):
        return None
    if latest_attempt is not None:
        since_attempt = now_epoch - _as_utc(latest_attempt.attempted_at).timestamp()
        if latest_attempt.status != "failed" and since_attempt < _OPENAI_PRIME_SPACING_SECONDS:
            return None
        if (
            latest_attempt.window == _OPENAI_CONTINUOUS_WINDOW
            and latest_attempt.status == "failed"
            and latest_attempt.retry_count >= _MAX_CONTINUOUS_RETRIES
            and since_attempt < _OPENAI_FAILURE_BRAKE_SECONDS
        ):
            return None
    return int(now_epoch // _OPENAI_PRIME_BUCKET_SECONDS) * _OPENAI_PRIME_BUCKET_SECONDS


def _opened_by_prime(attempt: AccountLimitWarmup, reset_at: int) -> bool:
    sent_at = _as_utc(attempt.attempted_at).timestamp()
    return sent_at + 4 * 3600 <= reset_at <= sent_at + 6 * 3600


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _primary_window_minutes(account: Account, primary_entry: UsageHistory | None) -> int:

    explicit = getattr(account, "window_minutes_primary", None)
    if isinstance(explicit, int) and explicit > 0:
        return explicit
    if primary_entry is not None and primary_entry.window_minutes:
        return int(primary_entry.window_minutes)
    return _DEFAULT_PRIMARY_WINDOW_MINUTES


def _primary_window_is_cold(
    account: Account,
    primary_entry: UsageHistory | None,
    current: datetime,
) -> bool:

    reset_at = primary_entry.reset_at if primary_entry is not None else None
    if reset_at is None:
        reset_at = getattr(account, "reset_at", None)
    if reset_at is None:
        return True
    return float(reset_at) <= current.timestamp()


def _planner_day_bounds(
    current: datetime,
    planner: PlannerSettings,
    window_minutes: int,
) -> tuple[datetime, datetime, datetime, datetime, datetime]:

    local_now = _to_planner_tz(current, planner.timezone)
    work_start = datetime.combine(
        local_now.date(),
        _parse_hhmm(planner.working_hours_start, _DEFAULT_WORKING_HOURS_START),
        tzinfo=local_now.tzinfo,
    )
    work_end = datetime.combine(
        local_now.date(),
        _parse_hhmm(planner.working_hours_end, _DEFAULT_WORKING_HOURS_END),
        tzinfo=local_now.tzinfo,
    )
    target_reset = work_start + timedelta(minutes=max(0, planner.seed_target_offset_minutes))
    seed_start = target_reset - timedelta(minutes=max(0, window_minutes))
    return local_now, seed_start, work_start, work_end, target_reset


def _within_seed_window(current: datetime, planner: PlannerSettings, window_minutes: int) -> bool:

    local_now, seed_start, work_start, _, _ = _planner_day_bounds(current, planner, window_minutes)
    return seed_start <= local_now < work_start


def _within_hold_window(current: datetime, planner: PlannerSettings, window_minutes: int) -> bool:

    local_now, seed_start, _, work_end, _ = _planner_day_bounds(current, planner, window_minutes)
    return seed_start <= local_now <= work_end


def _seed_reset_at(current: datetime, planner: PlannerSettings) -> int:

    _, _, _, _, target_reset = _planner_day_bounds(current, planner, _DEFAULT_PRIMARY_WINDOW_MINUTES)
    return int(target_reset.timestamp())


def _build_candidate(
    *,
    account: Account,
    window: str,
    before_primary: dict[str, UsageHistory],
    before_secondary: dict[str, UsageHistory],
    after_primary: dict[str, UsageHistory],
    after_secondary: dict[str, UsageHistory],
    min_available_percent: float,
) -> _WarmupCandidate | None:
    before = _effective_usage_entry(
        account.id,
        window=window,
        primary=before_primary,
        secondary=before_secondary,
    )
    after = _effective_usage_entry(
        account.id,
        window=window,
        primary=after_primary,
        secondary=after_secondary,
    )
    if before is None or after is None:
        return None
    if before.reset_at is None or after.reset_at is None:
        return None
    if before.used_percent < 100.0:
        return None
    if after.used_percent >= 100.0:
        return None
    available_percent = 100.0 - after.used_percent
    if min_available_percent < 100.0 and available_percent < min_available_percent:
        return None
    if after.reset_at <= before.reset_at:
        return None
    return _WarmupCandidate(reset_at=after.reset_at)


def _effective_usage_entry(
    account_id: str,
    *,
    window: str,
    primary: dict[str, UsageHistory],
    secondary: dict[str, UsageHistory],
) -> UsageHistory | None:
    if window == "primary":
        primary_entry = primary.get(account_id)
        if primary_entry is None or usage_core.is_weekly_window_minutes(primary_entry.window_minutes):
            return None
        return primary_entry

    primary_entry = primary.get(account_id)
    secondary_entry = secondary.get(account_id)
    if primary_entry is not None and usage_core.is_weekly_window_minutes(primary_entry.window_minutes):
        if secondary_entry is None:
            return primary_entry
        if usage_core.should_use_weekly_primary(
            usage_history_to_window_row(primary_entry),
            usage_history_to_window_row(secondary_entry),
        ):
            return primary_entry
    return secondary_entry


def _truncate(value: str | None, limit: int = 1000) -> str | None:
    if value is None:
        return None
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "..."
