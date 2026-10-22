from __future__ import annotations

from datetime import datetime, timezone

from app.core import usage as usage_core
from app.core.auth import DEFAULT_PLAN
from app.core.config import settings as config_settings
from app.core.crypto import TokenEncryptor
from app.core.plan_types import coerce_account_plan_type
from app.core.providers import ANTHROPIC_PROVIDER_NAME, OPENAI_PROVIDER_NAME, normalize_provider_name
from app.core.usage.types import UsageTrendBucket, UsageWindowRow
from app.core.utils.time import from_epoch_seconds, utcnow
from app.db.models import Account, AccountLimitWarmup, AccountStatus, AdditionalUsageHistory, UsageHistory
from app.modules.accounts import reset_credit_cache
from app.modules.accounts.auth_contracts import is_locally_owned
from app.modules.accounts.credits import (
    OPENROUTER_PROVIDER_NAME,
    credits_exhausted,
    window_from_usage,
)
from app.modules.accounts.health import account_problems
from app.modules.accounts.schemas import (
    AccountAdditionalQuota,
    AccountCreditsWindow,
    AccountRequestUsage,
    AccountSummary,
    AccountUsage,
    AccountUsageTrend,
    UsageTrendPoint,
)
from app.modules.accounts.summary_state import (
    _account_subscription_ledger,
    _build_auth_status,
    _duplicate_detection_key,
    _duplicate_detection_keys_appearing_more_than_once,
    _effective_status_from_usage,
    _effective_usage_windows,
    _extract_credit_status,
    _fable_eligible,
    _fable_scoped_weekly_summary,
    _identity_mismatch_status,
    _limit_warmup_to_status,
    _normalize_account_routing_policy,
    _normalize_used_percent,
    _openrouter_additional_quotas,
    _usage_entry_is_recent_enough,
)


def build_account_summaries(
    *,
    accounts: list[Account],
    primary_usage: dict[str, UsageHistory],
    secondary_usage: dict[str, UsageHistory],
    monthly_usage: dict[str, UsageHistory] | None = None,
    credits_usage: dict[str, UsageHistory] | None = None,
    request_usage_by_account: dict[str, AccountRequestUsage] | None = None,
    additional_quotas_by_account: dict[str, list[AccountAdditionalQuota]] | None = None,
    limit_warmups_by_account: dict[str, AccountLimitWarmup] | None = None,
    last_primed_by_account: dict[str, datetime] | None = None,
    fable_scoped_weekly_by_account: dict[str, AdditionalUsageHistory] | None = None,
    encryptor: TokenEncryptor,
    include_auth: bool = True,
    owner_source_names: dict[str, str] | None = None,
) -> list[AccountSummary]:
    duplicate_keys = _duplicate_detection_keys_appearing_more_than_once(accounts)
    return [
        _account_to_summary(
            account,
            primary_usage.get(account.id),
            secondary_usage.get(account.id),
            monthly_usage.get(account.id) if monthly_usage else None,
            credits_usage.get(account.id) if credits_usage else None,
            request_usage_by_account.get(account.id) if request_usage_by_account else None,
            additional_quotas_by_account.get(account.id) if additional_quotas_by_account else None,
            limit_warmups_by_account.get(account.id) if limit_warmups_by_account else None,
            encryptor,
            last_primed_at=last_primed_by_account.get(account.id) if last_primed_by_account else None,
            include_auth=include_auth,
            owner_source_names=owner_source_names,
            is_email_duplicate=_duplicate_detection_key(account) in duplicate_keys,
            fable_scoped_weekly=(
                fable_scoped_weekly_by_account.get(account.id) if fable_scoped_weekly_by_account else None
            ),
        )
        for account in accounts
    ]


def _account_to_summary(
    account: Account,
    primary_usage: UsageHistory | None,
    secondary_usage: UsageHistory | None,
    monthly_usage: UsageHistory | None,
    credits_usage: UsageHistory | None,
    request_usage: AccountRequestUsage | None,
    additional_quotas: list[AccountAdditionalQuota] | None,
    limit_warmup: AccountLimitWarmup | None,
    encryptor: TokenEncryptor,
    last_primed_at: datetime | None = None,
    include_auth: bool = True,
    is_email_duplicate: bool = False,
    fable_scoped_weekly: AdditionalUsageHistory | None = None,
    owner_source_names: dict[str, str] | None = None,
) -> AccountSummary:
    plan_type = coerce_account_plan_type(account.plan_type, DEFAULT_PLAN)
    auth_status = _build_auth_status(account, encryptor) if include_auth else None
    effective_primary_usage, effective_secondary_usage = _effective_usage_windows(
        primary_usage,
        secondary_usage,
    )

    if monthly_usage is not None and usage_core.capacity_for_plan(plan_type, "monthly") is None:
        monthly_usage = None
    monthly_used_percent = _normalize_used_percent(monthly_usage)
    monthly_remaining_percent = usage_core.remaining_percent_from_used(monthly_used_percent)
    if monthly_usage is not None:
        effective_primary_usage = None
        effective_secondary_usage = None

    weekly_only_usage = (
        effective_primary_usage is None
        and primary_usage is not None
        and usage_core.is_weekly_window_minutes(primary_usage.window_minutes)
    )

    primary_used_percent = _normalize_used_percent(effective_primary_usage)
    secondary_used_percent = _normalize_used_percent(effective_secondary_usage)
    primary_remaining_percent = usage_core.remaining_percent_from_used(primary_used_percent)
    secondary_remaining_percent = usage_core.remaining_percent_from_used(secondary_used_percent)

    if (
        primary_remaining_percent is None
        and not weekly_only_usage
        and normalize_provider_name(account.provider) not in (ANTHROPIC_PROVIDER_NAME, OPENAI_PROVIDER_NAME)
    ):
        primary_remaining_percent = 100.0

    status_primary_usage = effective_primary_usage
    status_primary_used_percent = primary_used_percent
    status_runtime_reset = float(account.reset_at) if account.reset_at else None
    status_seed = account.status
    allow_missing_runtime_reset_recovery = False
    long_quota_usage = monthly_usage or effective_secondary_usage
    long_quota_available = (
        long_quota_usage is not None
        and _usage_entry_is_recent_enough(long_quota_usage.recorded_at)
        and long_quota_usage.used_percent is not None
        and float(long_quota_usage.used_percent) < 100.0
    )
    if usage_core.capacity_for_plan(plan_type, "primary") == 0.0:
        runtime_reset_elapsed = (
            account.status == AccountStatus.RATE_LIMITED
            and account.reset_at is not None
            and account.reset_at <= utcnow().timestamp()
        )
        primary_window_minutes = (
            effective_primary_usage.window_minutes
            if effective_primary_usage is not None
            else primary_usage.window_minutes
            if weekly_only_usage and primary_usage is not None
            else None
        )
        zero_capacity_primary_known_non_primary = (
            primary_window_minutes is not None and not usage_core.is_primary_window_minutes(primary_window_minutes)
        )
        keep_primary_status_signal = (
            account.status == AccountStatus.RATE_LIMITED
            and usage_core.is_primary_window_minutes(primary_window_minutes)
        )
        can_hide_zero_capacity_primary = account.status != AccountStatus.RATE_LIMITED or (
            zero_capacity_primary_known_non_primary and long_quota_available
        )
        if not keep_primary_status_signal and can_hide_zero_capacity_primary:
            status_primary_usage = None
            status_primary_used_percent = None
            if account.status == AccountStatus.RATE_LIMITED:
                status_runtime_reset = None
                status_seed = AccountStatus.ACTIVE
                allow_missing_runtime_reset_recovery = True
        elif runtime_reset_elapsed and long_quota_available:
            status_primary_usage = None
            status_primary_used_percent = None
            status_runtime_reset = None
            status_seed = AccountStatus.ACTIVE
            allow_missing_runtime_reset_recovery = True
        effective_primary_usage = None
        primary_used_percent = None
        primary_remaining_percent = None

    reset_at_primary = (
        from_epoch_seconds(effective_primary_usage.reset_at) if effective_primary_usage is not None else None
    )
    reset_at_secondary = (
        from_epoch_seconds(effective_secondary_usage.reset_at) if effective_secondary_usage is not None else None
    )
    reset_at_monthly = from_epoch_seconds(monthly_usage.reset_at) if monthly_usage is not None else None
    window_minutes_primary = effective_primary_usage.window_minutes if effective_primary_usage is not None else None
    window_minutes_secondary = (
        effective_secondary_usage.window_minutes if effective_secondary_usage is not None else None
    )
    window_minutes_monthly = monthly_usage.window_minutes if monthly_usage is not None else None
    capacity_primary = usage_core.capacity_for_plan(plan_type, "primary")
    capacity_secondary = usage_core.capacity_for_plan(plan_type, "secondary")
    capacity_monthly = usage_core.capacity_for_plan(plan_type, "monthly")
    remaining_credits_primary = usage_core.remaining_credits_from_percent(
        primary_used_percent,
        capacity_primary,
    )
    remaining_credits_secondary = usage_core.remaining_credits_from_percent(
        secondary_used_percent,
        capacity_secondary,
    )
    remaining_credits_monthly = usage_core.remaining_credits_from_percent(
        monthly_used_percent,
        capacity_monthly,
    )
    credits_has, credits_unlimited, credits_balance = _extract_credit_status(
        effective_primary_usage,
        effective_secondary_usage,
        monthly_usage,
        primary_usage,
        secondary_usage,
    )
    effective_status = _effective_status_from_usage(
        account,
        status_seed=status_seed,
        primary_usage=status_primary_usage,
        primary_used_percent=status_primary_used_percent,
        secondary_usage=effective_secondary_usage,
        secondary_used_percent=secondary_used_percent,
        monthly_usage=monthly_usage,
        monthly_used_percent=monthly_used_percent,
        runtime_reset=status_runtime_reset,
        credits_has=credits_has,
        credits_unlimited=credits_unlimited,
        credits_balance=credits_balance,
        allow_missing_runtime_reset_recovery=allow_missing_runtime_reset_recovery,
    )

    credits_cap = None
    credits_spent = None
    credits_window_model = None
    if normalize_provider_name(account.provider) == OPENROUTER_PROVIDER_NAME:
        openrouter_window = window_from_usage(credits_usage)
        if openrouter_window is not None:
            credits_has = True
            credits_unlimited = False
            credits_balance = openrouter_window.balance
            credits_cap = openrouter_window.cap
            credits_spent = openrouter_window.spent
            credits_window_model = AccountCreditsWindow(
                balance=openrouter_window.balance,
                cap=openrouter_window.cap,
            )
            if credits_exhausted(openrouter_window):
                effective_status = AccountStatus.QUOTA_EXCEEDED
        additional_quotas = _openrouter_additional_quotas(additional_quotas)
    account_is_locally_owned = is_locally_owned(account, config_settings.get_settings())
    return AccountSummary(
        problems=account_problems(account, primary_usage, secondary_usage),
        account_id=account.id,
        provider=account.provider,
        email=account.email,
        alias=account.alias,
        display_name=account.alias or account.email,
        workspace_id=account.workspace_id,
        workspace_label=account.workspace_label,
        seat_type=account.seat_type,
        plan_type=plan_type,
        status=effective_status.value,
        routing_policy=_normalize_account_routing_policy(account.routing_policy),
        security_work_authorized=bool(account.security_work_authorized),
        fable_eligible=_fable_eligible(account, secondary_used_percent, fable_scoped_weekly),
        fable_scoped_weekly=_fable_scoped_weekly_summary(account, fable_scoped_weekly),
        usage=AccountUsage(
            primary_remaining_percent=primary_remaining_percent,
            secondary_remaining_percent=secondary_remaining_percent,
            monthly_remaining_percent=monthly_remaining_percent,
        ),
        reset_at_primary=reset_at_primary,
        reset_at_secondary=reset_at_secondary,
        reset_at_monthly=reset_at_monthly,
        rate_limit_reset_at=from_epoch_seconds(account.reset_at) if account.reset_at else None,
        window_minutes_primary=window_minutes_primary,
        window_minutes_secondary=window_minutes_secondary,
        window_minutes_monthly=window_minutes_monthly,
        last_refresh_at=account.last_refresh,
        capacity_credits_primary=capacity_primary,
        remaining_credits_primary=remaining_credits_primary,
        capacity_credits_secondary=capacity_secondary,
        remaining_credits_secondary=remaining_credits_secondary,
        capacity_credits_monthly=capacity_monthly,
        remaining_credits_monthly=remaining_credits_monthly,
        credits_has=credits_has,
        credits_unlimited=credits_unlimited,
        credits_balance=credits_balance,
        credits_cap=credits_cap,
        credits_spent=credits_spent,
        credits=credits_window_model,
        reset_credits_available=(
            reset_credit_cache.get_count(account.id)
            if normalize_provider_name(account.provider) in (OPENAI_PROVIDER_NAME, ANTHROPIC_PROVIDER_NAME)
            else None
        ),
        subscription=_account_subscription_ledger(account),
        request_usage=request_usage,
        additional_quotas=additional_quotas or [],
        deactivation_reason=account.deactivation_reason,
        auth=auth_status,
        limit_warmup_enabled=bool(account.limit_warmup_enabled),
        limit_warmup=_limit_warmup_to_status(limit_warmup),
        last_primed_at=last_primed_at,
        is_email_duplicate=is_email_duplicate,
        identity_mismatch=_identity_mismatch_status(account.id),
        owner_instance=account.owner_instance,
        is_locally_owned=account_is_locally_owned,
        owner_label=(
            (owner_source_names or {}).get(account.owner_instance, account.owner_instance)
            if not account_is_locally_owned and account.owner_instance
            else None
        ),
    )


def build_account_usage_trends(
    buckets: list[UsageTrendBucket],
    since_epoch: int,
    bucket_seconds: int,
    bucket_count: int,
) -> dict[str, AccountUsageTrend]:

    grouped: dict[tuple[str, str], dict[int, float]] = {}
    secondary_schedule: dict[str, dict[int, tuple[int, int]]] = {}
    for b in _effective_usage_trend_buckets(buckets):
        is_weekly_primary = b.window == "primary" and usage_core.is_weekly_window_minutes(b.window_minutes)
        window = "secondary" if is_weekly_primary or b.window == "monthly" else b.window
        key = (b.account_id, window)
        grouped.setdefault(key, {})[b.bucket_epoch] = b.avg_used_percent
        if (
            (window == "secondary" or usage_core.is_weekly_window_minutes(b.window_minutes))
            and b.reset_at is not None
            and b.window_minutes
        ):
            secondary_schedule.setdefault(b.account_id, {})[b.bucket_epoch] = (
                b.reset_at,
                b.window_minutes,
            )

    aligned_start = (since_epoch // bucket_seconds) * bucket_seconds
    time_grid = [aligned_start + i * bucket_seconds for i in range(bucket_count)]

    result: dict[str, AccountUsageTrend] = {}

    account_ids = {key[0] for key in grouped}

    for account_id in account_ids:
        primary_data = grouped.get((account_id, "primary"))
        secondary_data = grouped.get((account_id, "secondary"))

        primary_points = _fill_trend_points(time_grid, primary_data) if primary_data else []
        secondary_points = _fill_trend_points(time_grid, secondary_data) if secondary_data else []
        secondary_scheduled_points = _fill_scheduled_secondary_points(
            time_grid,
            secondary_schedule.get(account_id, {}),
        )

        result[account_id] = AccountUsageTrend(
            primary=primary_points,
            secondary=secondary_points,
            secondary_scheduled=secondary_scheduled_points,
        )

    return result


def _effective_usage_trend_buckets(buckets: list[UsageTrendBucket]) -> list[UsageTrendBucket]:
    secondary_by_key = {
        (bucket.account_id, bucket.bucket_epoch): bucket for bucket in buckets if bucket.window == "secondary"
    }
    weekly_primary_by_key = {
        (bucket.account_id, bucket.bucket_epoch): bucket
        for bucket in buckets
        if bucket.window == "primary" and usage_core.is_weekly_window_minutes(bucket.window_minutes)
    }
    result: list[UsageTrendBucket] = []
    for bucket in buckets:
        key = (bucket.account_id, bucket.bucket_epoch)
        weekly_primary = weekly_primary_by_key.get(key)
        if bucket.window == "secondary" and weekly_primary is not None:
            if usage_core.should_use_weekly_primary(
                _trend_bucket_to_window_row(weekly_primary),
                _trend_bucket_to_window_row(bucket),
            ):
                continue
        if bucket is weekly_primary and key in secondary_by_key:
            secondary = secondary_by_key[key]
            if not usage_core.should_use_weekly_primary(
                _trend_bucket_to_window_row(bucket),
                _trend_bucket_to_window_row(secondary),
            ):
                continue
        result.append(bucket)
    return result


def _trend_bucket_to_window_row(bucket: UsageTrendBucket) -> UsageWindowRow:
    return UsageWindowRow(
        account_id=bucket.account_id,
        used_percent=bucket.avg_used_percent,
        reset_at=bucket.reset_at,
        window_minutes=bucket.window_minutes,
        recorded_at=bucket.recorded_at,
    )


def _fill_trend_points(
    time_grid: list[int],
    bucket_data: dict[int, float],
) -> list[UsageTrendPoint]:

    points: list[UsageTrendPoint] = []
    last_value = 100.0
    for epoch in time_grid:
        if epoch in bucket_data:
            remaining = max(0.0, min(100.0, 100.0 - bucket_data[epoch]))
            last_value = remaining
        else:
            remaining = last_value
        points.append(
            UsageTrendPoint(
                t=datetime.fromtimestamp(epoch, tz=timezone.utc),
                v=round(remaining, 2),
            )
        )
    return points


def _fill_scheduled_secondary_points(
    time_grid: list[int],
    schedule_data: dict[int, tuple[int, int]],
) -> list[UsageTrendPoint]:

    points: list[UsageTrendPoint] = []
    current_reset_at: int | None = None
    current_window_minutes: int | None = None

    for epoch in time_grid:
        if epoch in schedule_data:
            current_reset_at, current_window_minutes = schedule_data[epoch]

        if current_reset_at is None or not current_window_minutes:
            continue

        window_seconds = current_window_minutes * 60
        remaining_seconds = max(0, min(window_seconds, current_reset_at - epoch))
        scheduled_remaining = 100.0 * remaining_seconds / window_seconds
        points.append(
            UsageTrendPoint(
                t=datetime.fromtimestamp(epoch, tz=timezone.utc),
                v=round(scheduled_remaining, 2),
            )
        )

    return points
