from __future__ import annotations

from datetime import datetime, timezone

from app.core import usage as usage_core
from app.core.usage.types import UsageTrendBucket, UsageWindowRow
from app.modules.accounts.schemas import AccountUsageTrend, UsageTrendPoint


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
