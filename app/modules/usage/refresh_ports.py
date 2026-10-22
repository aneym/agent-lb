from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.db.models import AccountStatus, UsageHistory
from app.modules.accounts.auth_manager import AccountsRepositoryPort


class UsageRepositoryPort(Protocol):
    async def latest_entry_for_account(
        self,
        account_id: str,
        *,
        window: str | None = None,
    ) -> UsageHistory | None: ...

    async def add_entry(
        self,
        account_id: str,
        used_percent: float,
        provider: str = "openai",
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        recorded_at: datetime | None = None,
        window: str | None = None,
        reset_at: int | None = None,
        window_minutes: int | None = None,
        credits_has: bool | None = None,
        credits_unlimited: bool | None = None,
        credits_balance: float | None = None,
    ) -> UsageHistory | None: ...


class AdditionalUsageRepositoryPort(Protocol):
    async def add_entry(
        self,
        account_id: str,
        limit_name: str,
        metered_feature: str,
        window: str,
        used_percent: float,
        reset_at: int | None = None,
        window_minutes: int | None = None,
        recorded_at: datetime | None = None,
        quota_key: str | None = None,
    ) -> None: ...

    async def delete_for_account(self, account_id: str) -> None: ...

    async def delete_for_account_and_quota_key(self, account_id: str, quota_key: str) -> None: ...

    async def delete_for_account_and_limit(self, account_id: str, limit_name: str) -> None: ...

    async def delete_for_account_quota_key_window(
        self,
        account_id: str,
        quota_key: str,
        window: str,
    ) -> None: ...

    async def delete_for_account_limit_window(
        self,
        account_id: str,
        limit_name: str,
        window: str,
    ) -> None: ...

    async def list_quota_keys(
        self,
        *,
        account_ids: Collection[str] | None = None,
        since: datetime | None = None,
    ) -> list[str]: ...

    async def list_limit_names(
        self,
        *,
        account_ids: Collection[str] | None = None,
        since: datetime | None = None,
    ) -> list[str]: ...

    async def latest_recorded_at_for_account(self, account_id: str) -> datetime | None: ...


class AccountsRepositoryWithStatusComparePort(AccountsRepositoryPort, Protocol):
    async def update_status_if_current(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        blocked_at: int | None = None,
        *,
        expected_status: AccountStatus,
        expected_deactivation_reason: str | None = None,
        expected_reset_at: int | None = None,
        expected_blocked_at: int | None = None,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class AccountRefreshResult:
    usage_written: bool
    fetch_succeeded: bool = True
