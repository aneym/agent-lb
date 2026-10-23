from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, or_, select, update

from app.core.config.settings import get_settings
from app.db.account_state_models import AccountExchangeIntent
from app.db.models import Account, AccountStatus
from app.db.session import sqlite_writer_section
from app.modules.accounts.repository_state import _UNSET


class AccountCredentialRepository:
    async def rotate_tokens(
        self,
        account_id: str,
        access_token_encrypted: bytes,
        refresh_token_encrypted: bytes,
        id_token_encrypted: bytes,
        last_refresh: datetime,
        *,
        expected_refresh_token_encrypted: bytes,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
        chatgpt_user_id: str | None = None,
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
    ) -> bool:
        async with sqlite_writer_section():
            if self._dialect_name() == "postgresql":
                await self._lock_postgresql_account_identity_membership(account_id, chatgpt_account_id)
            values: dict[str, bytes | datetime | str] = {
                "access_token_encrypted": access_token_encrypted,
                "refresh_token_encrypted": refresh_token_encrypted,
                "id_token_encrypted": id_token_encrypted,
                "last_refresh": last_refresh,
            }
            if plan_type is not None:
                values["plan_type"] = plan_type
            if email is not None:
                values["email"] = email
            if chatgpt_account_id is not None:
                values["chatgpt_account_id"] = chatgpt_account_id
            if chatgpt_user_id is not None:
                values["chatgpt_user_id"] = chatgpt_user_id
            if workspace_id is not None:
                values["workspace_id"] = workspace_id
            if workspace_label is not None:
                values["workspace_label"] = workspace_label
            if seat_type is not None:
                values["seat_type"] = seat_type
            stmt = (
                update(Account)
                .where(Account.id == account_id)
                .where(Account.refresh_token_encrypted == expected_refresh_token_encrypted)
                .values(**values)
                .returning(Account.id)
            )
            result = await self._session.execute(stmt)
            await self._session.commit()
            return result.scalar_one_or_none() is not None

    async def update_account_metadata(
        self,
        account_id: str,
        *,
        plan_type: str | None = None,
        email: str | None = None,
        chatgpt_account_id: str | None = None,
        chatgpt_user_id: str | None = None,
        workspace_id: str | None = None,
        workspace_label: str | None = None,
        seat_type: str | None = None,
        last_refresh: datetime | None = None,
    ) -> bool:
        async with sqlite_writer_section():
            if self._dialect_name() == "postgresql":
                await self._lock_postgresql_account_identity_membership(account_id, chatgpt_account_id)
            values: dict[str, str | datetime] = {}
            if plan_type is not None:
                values["plan_type"] = plan_type
            if email is not None:
                values["email"] = email
            if chatgpt_account_id is not None:
                values["chatgpt_account_id"] = chatgpt_account_id
            if chatgpt_user_id is not None:
                values["chatgpt_user_id"] = chatgpt_user_id
            if workspace_id is not None:
                values["workspace_id"] = workspace_id
            if workspace_label is not None:
                values["workspace_label"] = workspace_label
            if seat_type is not None:
                values["seat_type"] = seat_type
            if last_refresh is not None:
                values["last_refresh"] = last_refresh
            if not values:
                existing = await self._session.get(Account, account_id)
                return existing is not None
            result = await self._session.execute(
                update(Account).where(Account.id == account_id).values(**values).returning(Account.id)
            )
            await self._session.commit()
            return result.scalar_one_or_none() is not None

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
        access_expires_at: datetime | None | object = _UNSET,
        exchange_token_hash: str | None = None,
        expected_refresh_token_encrypted: bytes | None = None,
        chatgpt_user_id: str | None = None,
    ) -> bool:
        values: dict[str, bytes | datetime | str | None] = {
            "access_token_encrypted": access_token_encrypted,
            "refresh_token_encrypted": refresh_token_encrypted,
            "id_token_encrypted": id_token_encrypted,
            "last_refresh": last_refresh,
        }
        if access_expires_at is not _UNSET:
            values["access_expires_at"] = access_expires_at
        if exchange_token_hash is not None:
            values["status"] = AccountStatus.ACTIVE
            values["deactivation_reason"] = None
        if plan_type is not None:
            values["plan_type"] = plan_type
        if email is not None:
            values["email"] = email
        if chatgpt_account_id is not None:
            values["chatgpt_account_id"] = chatgpt_account_id
        if chatgpt_user_id is not None:
            values["chatgpt_user_id"] = chatgpt_user_id
        if workspace_id is not None:
            values["workspace_id"] = workspace_id
        if workspace_label is not None:
            values["workspace_label"] = workspace_label
        if seat_type is not None:
            values["seat_type"] = seat_type
        stmt = update(Account).where(Account.id == account_id)
        if expected_refresh_token_encrypted is not None:
            stmt = stmt.where(Account.refresh_token_encrypted == expected_refresh_token_encrypted)
        if exchange_token_hash is not None:
            stmt = stmt.where(
                Account.id.in_(
                    select(Account.id).where(
                        or_(
                            Account.owner_instance.is_(None), Account.owner_instance == get_settings().local_instance_id
                        )
                    )
                )
            ).where(
                Account.id.in_(
                    select(AccountExchangeIntent.account_id).where(
                        AccountExchangeIntent.account_id == account_id,
                        AccountExchangeIntent.refresh_token_sha256 == exchange_token_hash,
                    )
                )
            )
        with self._session.no_autoflush:
            result = await self._session.execute(stmt.values(**values).returning(Account.id))
        if result.scalar_one_or_none() is None:
            await self._session.rollback()
            return False
        if exchange_token_hash is not None:
            await self._session.execute(
                delete(AccountExchangeIntent).where(
                    AccountExchangeIntent.account_id == account_id,
                    AccountExchangeIntent.refresh_token_sha256 == exchange_token_hash,
                )
            )
        await self._session.commit()
        return True
