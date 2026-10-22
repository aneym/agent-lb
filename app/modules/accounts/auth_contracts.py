from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.core.config.settings import Settings
from app.db.models import Account, AccountStatus


class AccountsRepositoryPort(Protocol):
    async def get_by_id(self, account_id: str) -> Account | None: ...

    async def reload_by_id(self, account_id: str) -> Account | None: ...

    async def exchange_intent_hash(self, account_id: str) -> str | None: ...

    async def drop_stale_exchange_intent(self, account_id: str, stale_hash: str, expected_token: bytes) -> bool: ...

    async def begin_exchange(
        self,
        account_id: str,
        token_hash: str,
        expected_token: bytes,
        *,
        replay: bool = False,
        timeout_seconds: float | None = None,
    ) -> bool: ...

    async def mark_exchange_uncertain(
        self, account_id: str, token_hash: str, expected_token: bytes, *, reason: str | None = None
    ) -> None: ...

    async def abort_exchange(self, account_id: str, token_hash: str) -> None: ...

    async def update_status(
        self,
        account_id: str,
        status: AccountStatus,
        deactivation_reason: str | None = None,
        reset_at: int | None = None,
        blocked_at: int | None = None,
        *,
        expected_refresh_token_encrypted: bytes | None = None,
    ) -> bool: ...

    async def update_tokens(
        self,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes,
        id_token_encrypted: bytes | None,
        last_refresh: datetime,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
        *,
        expected_refresh_token_encrypted: bytes | None = None,
        access_expires_at: datetime | None = None,
        exchange_token_hash: str | None = None,
    ) -> bool: ...

    async def workspace_slot_taken(
        self,
        *,
        account_id: str,
        email: str,
        chatgpt_account_id: str | None,
        workspace_id: str,
    ) -> bool: ...


class RefreshAdmissionLeasePort(Protocol):
    def release(self) -> None: ...


class AccountNotOwnedError(Exception):
    def __init__(self, account_id: str, owner_instance: str | None, local_instance_id: str) -> None:
        message = (
            f"Account {account_id} is owned by instance {owner_instance!r}, not the "
            f"local instance {local_instance_id!r}; refusing to refresh"
        )
        super().__init__(message)
        self.account_id = account_id
        self.owner_instance = owner_instance
        self.local_instance_id = local_instance_id


def is_locally_owned(account: Account, settings: Settings) -> bool:
    return account.owner_instance is None or account.owner_instance == settings.local_instance_id
