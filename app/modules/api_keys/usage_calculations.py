from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from app.core.usage.types import UsageWindowRow
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.api_keys.repository_data import ApiKeyTrendBucket
from app.modules.api_keys.service_data import ApiKeyTrendsData, ApiKeyTrendsPoint, PooledCreditData


def _compute_pooled_credits(
    *,
    assigned_account_ids: list[str],
    all_accounts: list[Account],
    primary_usage: dict[str, UsageHistory],
    secondary_usage: dict[str, UsageHistory],
    account_assignment_scope_enabled: bool = False,
) -> PooledCreditData:
    import app.core.usage as usage_core
    from app.modules.usage.mappers import usage_history_to_window_row

    if account_assignment_scope_enabled and (not assigned_account_ids):
        return PooledCreditData()
    if assigned_account_ids:
        requested_account_ids = set(assigned_account_ids)
    else:
        requested_account_ids = {a.id for a in all_accounts}
    account_map = {
        a.id: a
        for a in all_accounts
        if a.id in requested_account_ids
        and a.status not in (AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED, AccountStatus.PAUSED)
    }
    account_ids = set(account_map)
    primary_rows_raw = [
        usage_history_to_window_row(entry) for entry in primary_usage.values() if entry.account_id in account_ids
    ]
    secondary_rows_raw = [
        usage_history_to_window_row(entry) for entry in secondary_usage.values() if entry.account_id in account_ids
    ]
    primary_rows, secondary_rows = usage_core.normalize_weekly_only_rows(primary_rows_raw, secondary_rows_raw)
    now_epoch = int(time.time())
    has_live_primary = any((row.reset_at is None or row.reset_at > now_epoch for row in primary_rows))
    primary_rows = usage_core.expire_elapsed_window_rows(primary_rows, now_epoch=now_epoch)
    primary_rows = _seed_missing_usage_rows(primary_rows, account_ids)
    secondary_rows = _seed_missing_usage_rows(secondary_rows, account_ids)
    primary_summary = usage_core.summarize_usage_window(primary_rows, account_map, "primary")
    secondary_summary = usage_core.summarize_usage_window(secondary_rows, account_map, "secondary")
    primary_remaining = usage_core.remaining_percent_from_used(primary_summary.used_percent)
    secondary_remaining = usage_core.remaining_percent_from_used(secondary_summary.used_percent)
    if primary_summary.capacity_credits == 0.0 or not has_live_primary:
        primary_remaining = None
    return PooledCreditData(
        remaining_percent_primary=primary_remaining,
        remaining_percent_secondary=secondary_remaining,
        capacity_credits_primary=primary_summary.capacity_credits,
    )


def _seed_missing_usage_rows(rows: list[UsageWindowRow], account_ids: set[str]) -> list[UsageWindowRow]:
    present_account_ids = {row.account_id for row in rows}
    missing_account_ids = account_ids - present_account_ids
    if not missing_account_ids:
        return rows
    return rows + [
        UsageWindowRow(account_id=account_id, used_percent=0.0) for account_id in sorted(missing_account_ids)
    ]


def _build_api_key_trends(
    key_id: str, buckets: list[ApiKeyTrendBucket], since: datetime, until: datetime, bucket_seconds: int
) -> ApiKeyTrendsData:
    since_utc = since.replace(tzinfo=timezone.utc) if since.tzinfo is None else since.astimezone(timezone.utc)
    until_utc = until.replace(tzinfo=timezone.utc) if until.tzinfo is None else until.astimezone(timezone.utc)
    if until_utc <= since_utc:
        return ApiKeyTrendsData(key_id=key_id)
    start_epoch = int(since_utc.timestamp()) // bucket_seconds * bucket_seconds
    end_epoch = int((until_utc - timedelta(microseconds=1)).timestamp()) // bucket_seconds * bucket_seconds
    bucket_count = (end_epoch - start_epoch) // bucket_seconds + 1
    time_grid = [start_epoch + i * bucket_seconds for i in range(bucket_count)]
    cost_by_bucket: dict[int, float] = {}
    tokens_by_bucket: dict[int, int] = {}
    for b in buckets:
        cost_by_bucket[b.bucket_epoch] = b.total_cost_usd
        tokens_by_bucket[b.bucket_epoch] = b.total_tokens
    cost_points: list[ApiKeyTrendsPoint] = []
    tokens_points: list[ApiKeyTrendsPoint] = []
    for epoch in time_grid:
        dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
        cost_points.append(ApiKeyTrendsPoint(t=dt, v=round(cost_by_bucket.get(epoch, 0.0), 6)))
        tokens_points.append(ApiKeyTrendsPoint(t=dt, v=float(tokens_by_bucket.get(epoch, 0))))
    return ApiKeyTrendsData(key_id=key_id, cost=cost_points, tokens=tokens_points)
