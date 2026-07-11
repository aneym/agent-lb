from __future__ import annotations

import time
from datetime import datetime, timedelta

from app.core import usage as usage_core
from app.core.usage.types import UsageCostSummary, UsageMetricsSummary, UsageWindowRow
from app.core.utils.time import utcnow
from app.db.models import RequestLog
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.subscription_status import subscription_usable_accounts
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.builders import (
    _cost_summary_from_logs,
    _usage_metrics,
    build_usage_history_response,
    build_usage_summary_response,
    build_usage_window_response,
)
from app.modules.usage.mappers import usage_history_to_window_row
from app.modules.usage.repository import UsageRepository
from app.modules.usage.schemas import (
    UsageHistoryResponse,
    UsageSummaryResponse,
    UsageWindowResponse,
)

# Cached (metrics, cost) aggregates derived from the request-log window,
# keyed by (provider, window minutes, account-id set). Values are
# (monotonic timestamp, metrics, cost).
_LOG_METRICS_CACHE_TTL_SECONDS = 60.0
_LOG_METRICS_CACHE: dict[
    tuple[str, int, tuple[str, ...]],
    tuple[float, UsageMetricsSummary, UsageCostSummary],
] = {}


class UsageService:
    def __init__(
        self,
        usage_repo: UsageRepository,
        logs_repo: RequestLogsRepository,
        accounts_repo: AccountsRepository,
    ) -> None:
        self._usage_repo = usage_repo
        self._logs_repo = logs_repo
        self._accounts_repo = accounts_repo

    async def get_usage_summary(self, provider: str | None = None) -> UsageSummaryResponse:
        now = utcnow()
        accounts = subscription_usable_accounts(await self._accounts_repo.list_accounts())
        if provider:
            accounts = [account for account in accounts if account.provider.lower() == provider.lower()]
        account_ids = {account.id for account in accounts}

        primary_rows_raw = _usage_rows_for_accounts(await self._latest_usage_rows("primary"), account_ids)
        secondary_rows_raw = _usage_rows_for_accounts(await self._latest_usage_rows("secondary"), account_ids)
        monthly_rows_raw = _usage_rows_for_accounts(await self._latest_usage_rows("monthly"), account_ids)
        primary_rows, secondary_rows = usage_core.normalize_weekly_only_rows(
            primary_rows_raw,
            secondary_rows_raw,
        )

        secondary_minutes = usage_core.resolve_window_minutes("secondary", secondary_rows)
        metrics_override: UsageMetricsSummary | None = None
        cost_override: UsageCostSummary | None = None
        if secondary_minutes:
            metrics_override, cost_override = await self._log_window_metrics(
                now=now,
                secondary_minutes=secondary_minutes,
                account_ids=account_ids,
                provider=provider,
            )
        return build_usage_summary_response(
            accounts=accounts,
            primary_rows=primary_rows,
            secondary_rows=secondary_rows,
            monthly_rows=monthly_rows_raw,
            logs_secondary=[],
            metrics_override=metrics_override,
            cost_override=cost_override,
        )

    async def _log_window_metrics(
        self,
        *,
        now: datetime,
        secondary_minutes: int,
        account_ids: set[str],
        provider: str | None,
    ) -> tuple[UsageMetricsSummary, UsageCostSummary]:
        # Hydrating the full log window (observed >700k RequestLog rows for the
        # 7-day secondary window) blocks the event loop for seconds, and the
        # menubar polls this endpoint continuously — cache the derived
        # aggregates briefly so the cost is paid at most once per TTL.
        cache_key = (provider or "", secondary_minutes, tuple(sorted(account_ids)))
        mono_now = time.monotonic()
        cached = _LOG_METRICS_CACHE.get(cache_key)
        if cached is not None and mono_now - cached[0] < _LOG_METRICS_CACHE_TTL_SECONDS:
            return cached[1], cached[2]

        logs = await self._logs_repo.list_since(now - timedelta(minutes=secondary_minutes))
        logs = _logs_for_accounts(logs, account_ids, drop_unattributed=bool(provider))
        metrics = _usage_metrics(logs)
        cost = _cost_summary_from_logs(logs)
        for key in [k for k, v in _LOG_METRICS_CACHE.items() if mono_now - v[0] >= _LOG_METRICS_CACHE_TTL_SECONDS]:
            _LOG_METRICS_CACHE.pop(key, None)
        _LOG_METRICS_CACHE[cache_key] = (mono_now, metrics, cost)
        return metrics, cost

    async def get_usage_history(self, hours: int) -> UsageHistoryResponse:
        now = utcnow()
        since = now - timedelta(hours=hours)
        accounts = subscription_usable_accounts(await self._accounts_repo.list_accounts())
        account_ids = {account.id for account in accounts}
        usage_rows = _usage_rows_for_accounts(
            [row.to_window_row() for row in await self._usage_repo.aggregate_since(since, window="primary")],
            account_ids,
        )

        return build_usage_history_response(
            hours=hours,
            usage_rows=usage_rows,
            accounts=accounts,
            window="primary",
        )

    async def get_usage_window(self, window: str) -> UsageWindowResponse:
        window_key = (window or "").lower()
        if window_key not in {"primary", "secondary"}:
            raise ValueError("window must be 'primary' or 'secondary'")
        accounts = subscription_usable_accounts(await self._accounts_repo.list_accounts())
        account_ids = {account.id for account in accounts}
        primary_rows_raw = _usage_rows_for_accounts(await self._latest_usage_rows("primary"), account_ids)
        secondary_rows_raw = _usage_rows_for_accounts(await self._latest_usage_rows("secondary"), account_ids)
        primary_rows, secondary_rows = usage_core.normalize_weekly_only_rows(
            primary_rows_raw,
            secondary_rows_raw,
        )
        usage_rows = primary_rows if window_key == "primary" else secondary_rows
        window_minutes = usage_core.resolve_window_minutes(window_key, usage_rows)
        return build_usage_window_response(
            window_key=window_key,
            window_minutes=window_minutes,
            usage_rows=usage_rows,
            accounts=accounts,
        )

    async def _latest_usage_rows(self, window: str) -> list[UsageWindowRow]:
        latest = await self._usage_repo.latest_by_account(window=window)
        return [usage_history_to_window_row(entry) for entry in latest.values()]


def _usage_rows_for_accounts(rows: list[UsageWindowRow], account_ids: set[str]) -> list[UsageWindowRow]:
    return [row for row in rows if row.account_id in account_ids]


def _logs_for_accounts(
    logs: list[RequestLog],
    account_ids: set[str],
    *,
    drop_unattributed: bool = False,
) -> list[RequestLog]:
    if drop_unattributed:
        return [log for log in logs if log.account_id in account_ids]
    return [log for log in logs if log.account_id is None or log.account_id in account_ids]
