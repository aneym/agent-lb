from __future__ import annotations

from datetime import timedelta

from app.core import usage as usage_core
from app.core.usage.types import UsageWindowRow
from app.core.utils.time import utcnow
from app.db.models import RequestLog
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.subscription_status import subscription_usable_accounts
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.builders import (
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
        logs_secondary = []
        if secondary_minutes:
            logs_secondary = await self._logs_repo.list_since(now - timedelta(minutes=secondary_minutes))
            logs_secondary = _logs_for_accounts(logs_secondary, account_ids, drop_unattributed=bool(provider))
        return build_usage_summary_response(
            accounts=accounts,
            primary_rows=primary_rows,
            secondary_rows=secondary_rows,
            monthly_rows=monthly_rows_raw,
            logs_secondary=logs_secondary,
        )

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
