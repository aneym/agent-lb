from __future__ import annotations

from typing import Any

from sqlalchemy import delete, or_, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.providers import normalize_provider_name
from app.db.account_identity_lock import advisory_lock_key
from app.db.models import Account, AccountStatus, AdditionalUsageHistory, DashboardSettings, UsageHistory
from app.db.request_models import AccountLimitWarmup, ApiKeyAccountAssignment, RequestLog
from app.db.session import sqlite_writer_section
from app.db.session_models import HttpBridgeSessionRecord, StickySession
from app.modules.accounts.identity_values import _DUPLICATE_ACCOUNT_SUFFIX as _DUPLICATE_ACCOUNT_SUFFIX
from app.modules.accounts.identity_values import _SETTINGS_ROW_ID as _SETTINGS_ROW_ID
from app.modules.accounts.identity_values import _advisory_lock_key as _advisory_lock_key
from app.modules.accounts.identity_values import _apply_account_updates as _apply_account_updates
from app.modules.accounts.identity_values import _can_reuse_email_fallback as _can_reuse_email_fallback
from app.modules.accounts.identity_values import (
    _is_workspace_less_reauth_for_known_slot as _is_workspace_less_reauth_for_known_slot,
)
from app.modules.accounts.identity_values import _same_unknown_workspace_identity as _same_unknown_workspace_identity
from app.modules.accounts.identity_values import _slot_lock_keys as _slot_lock_keys
from app.modules.accounts.identity_values import _workspace_slot_identity as _workspace_slot_identity
from app.modules.accounts.identity_values import _workspace_slot_key as _workspace_slot_key
from app.modules.accounts.usage_rollup import lock_fold_state, merge_rollups_into
from app.modules.accounts.usage_time_rollup import merge_time_rollups_into


class AccountIdentityConflictError(Exception):
    def __init__(self, email: str) -> None:
        self.email = email
        super().__init__(
            f"Cannot overwrite account for email '{email}' because multiple matching accounts exist. Remove duplicates or enable import without overwrite."
        )


class AccountIdentityRepository:
    _session: AsyncSession

    async def exists_active_chatgpt_account_id(self, chatgpt_account_id: str) -> bool:
        return await self.get_active_by_chatgpt_account_id(chatgpt_account_id) is not None

    async def get_active_by_chatgpt_account_id(self, chatgpt_account_id: str) -> Account | None:
        result = await self._session.execute(
            select(Account)
            .where(Account.chatgpt_account_id == chatgpt_account_id)
            .where(
                Account.status.notin_((AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED, AccountStatus.PAUSED))
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def upsert(
        self, account: Account, *, merge_by_email: bool | None = None, merge_by_chatgpt_identity: bool = False
    ) -> Account:
        account.provider = normalize_provider_name(account.provider)
        async with sqlite_writer_section():
            return await self._upsert_unlocked(
                account, merge_by_email=merge_by_email, merge_by_chatgpt_identity=merge_by_chatgpt_identity
            )

    async def upsert_reauthorized(self, account: Account) -> Account:
        return await self.upsert_account_slot(account, preserve_unknown_workspace_duplicates=False)

    async def upsert_account_slot(
        self,
        account: Account,
        *,
        preserve_unknown_workspace_duplicates: bool | None = None,
        preserve_identity_slots: bool = False,
    ) -> Account:
        account.provider = normalize_provider_name(account.provider)
        async with sqlite_writer_section():
            return await self._upsert_account_slot_unlocked(
                account,
                preserve_unknown_workspace_duplicates=preserve_unknown_workspace_duplicates,
                preserve_identity_slots=preserve_identity_slots,
            )

    async def _account_by_chatgpt_identity(
        self, chatgpt_account_id: str, *, workspace_id: str | None, email: str | None, provider: str = "openai"
    ) -> Account | None:
        stmt = select(Account).where(Account.chatgpt_account_id == chatgpt_account_id, Account.provider == provider)
        order_by: list[Any] = [Account.created_at.asc(), Account.id.asc()]
        if workspace_id:
            stmt = stmt.where(or_(Account.workspace_id == workspace_id, Account.workspace_id.is_(None)))
            order_by.insert(0, Account.workspace_id.is_(None).asc())
        else:
            stmt = stmt.where(Account.workspace_id.is_(None))
        result = await self._session.execute(stmt.order_by(*order_by))
        candidates = list(result.scalars().all())
        if not candidates:
            return None
        if not email:
            return candidates[0]
        for candidate in candidates:
            if candidate.email == email:
                return candidate
        if len(candidates) == 1:
            return candidates[0]
        return None

    async def _reconcile_chatgpt_identity_duplicates(
        self,
        canonical: Account,
        chatgpt_account_id: str,
        workspace_id: str | None,
        email: str | None,
    ) -> bool:
        duplicate_stmt = select(Account.id).where(
            Account.chatgpt_account_id == chatgpt_account_id,
            Account.id != canonical.id,
            Account.provider == canonical.provider,
        )
        if email:
            duplicate_stmt = duplicate_stmt.where(Account.email == email)
        if workspace_id is None:
            duplicate_stmt = duplicate_stmt.where(Account.workspace_id.is_(None))
        else:
            duplicate_stmt = duplicate_stmt.where(Account.workspace_id == workspace_id)
        duplicate_accounts = (await self._session.execute(duplicate_stmt)).scalars().all()
        duplicate_ids = list(duplicate_accounts)
        if not duplicate_ids:
            return False
        await lock_fold_state(self._session)
        duplicate_api_key_ids = (
            (
                await self._session.execute(
                    select(ApiKeyAccountAssignment.api_key_id).where(ApiKeyAccountAssignment.account_id == canonical.id)
                )
            )
            .scalars()
            .all()
        )
        existing_api_key_ids = set(duplicate_api_key_ids)
        duplicate_assignments = (
            (
                await self._session.execute(
                    select(ApiKeyAccountAssignment).where(ApiKeyAccountAssignment.account_id.in_(duplicate_ids))
                )
            )
            .scalars()
            .all()
        )
        for assignment in duplicate_assignments:
            if assignment.api_key_id in existing_api_key_ids:
                await self._session.delete(assignment)
            else:
                assignment.account_id = canonical.id
                existing_api_key_ids.add(assignment.api_key_id)
        await self._session.execute(
            update(UsageHistory).where(UsageHistory.account_id.in_(duplicate_ids)).values(account_id=canonical.id)
        )
        await self._session.execute(
            update(AdditionalUsageHistory)
            .where(AdditionalUsageHistory.account_id.in_(duplicate_ids))
            .values(account_id=canonical.id)
        )
        await self._session.execute(
            update(RequestLog).where(RequestLog.account_id.in_(duplicate_ids)).values(account_id=canonical.id)
        )
        await self._reconcile_limit_warmups(canonical.id, duplicate_ids)
        await self._session.execute(
            update(StickySession).where(StickySession.account_id.in_(duplicate_ids)).values(account_id=canonical.id)
        )
        await self._session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.account_id.in_(duplicate_ids))
            .values(account_id=canonical.id)
        )
        await merge_rollups_into(self._session, canonical.id, duplicate_ids)
        await merge_time_rollups_into(self._session, canonical.id, duplicate_ids)
        await self._session.execute(delete(Account).where(Account.id.in_(duplicate_ids)))
        return True

    async def _reconcile_limit_warmups(self, canonical_account_id: str, duplicate_ids: list[str]) -> None:
        existing_keys = {
            (window, reset_at)
            for window, reset_at in (
                await self._session.execute(
                    select(AccountLimitWarmup.window, AccountLimitWarmup.reset_at).where(
                        AccountLimitWarmup.account_id == canonical_account_id
                    )
                )
            ).all()
        }
        duplicate_warmups = (
            (
                await self._session.execute(
                    select(AccountLimitWarmup).where(AccountLimitWarmup.account_id.in_(duplicate_ids))
                )
            )
            .scalars()
            .all()
        )
        for warmup in duplicate_warmups:
            key = (warmup.window, warmup.reset_at)
            if key in existing_keys:
                await self._session.delete(warmup)
            else:
                warmup.account_id = canonical_account_id
                existing_keys.add(key)

    async def _merge_by_email_enabled(self) -> bool:
        settings = await self._session.get(DashboardSettings, _SETTINGS_ROW_ID)
        if settings is None:
            return True
        return not settings.import_without_overwrite

    async def _next_available_account_id(self, base_id: str) -> str:
        candidate = base_id
        sequence = 2
        while await self._session.get(Account, candidate) is not None:
            candidate = f"{base_id}{_DUPLICATE_ACCOUNT_SUFFIX}{sequence}"
            sequence += 1
        return candidate

    async def _single_account_by_email(self, email: str, *, provider: str = "openai") -> Account | None:
        result = await self._session.execute(
            select(Account)
            .where(Account.email == email, Account.provider == provider)
            .order_by(Account.created_at.asc(), Account.id.asc())
            .limit(2)
        )
        matches = list(result.scalars().all())
        if not matches:
            return None
        if len(matches) > 1:
            raise AccountIdentityConflictError(email)
        return matches[0]

    async def _single_unknown_workspace_account_by_email(
        self, email: str, *, provider: str = "openai"
    ) -> Account | None:
        result = await self._session.execute(
            select(Account)
            .where(Account.email == email, Account.provider == provider)
            .where(Account.workspace_id.is_(None))
            .where(Account.workspace_label.is_(None))
            .order_by(Account.created_at.asc(), Account.id.asc())
            .limit(2)
        )
        matches = list(result.scalars().all())
        if not matches:
            return None
        if len(matches) > 1:
            raise AccountIdentityConflictError(email)
        return matches[0]

    async def _account_by_slot_identity(self, account: Account) -> Account | None:
        workspace_slot = _workspace_slot_identity(account)
        if account.chatgpt_account_id and account.email and workspace_slot:
            column, value = workspace_slot
            result = await self._session.execute(
                select(Account)
                .where(Account.provider == account.provider)
                .where(Account.chatgpt_account_id == account.chatgpt_account_id)
                .where(Account.email == account.email)
                .where(column == value)
                .order_by(Account.created_at.asc(), Account.id.asc())
                .limit(1)
            )
            if matched := result.scalar_one_or_none():
                return matched
        if account.chatgpt_account_id and account.email and account.workspace_id and account.workspace_label:
            result = await self._session.execute(
                select(Account)
                .where(Account.provider == account.provider)
                .where(Account.chatgpt_account_id == account.chatgpt_account_id)
                .where(Account.email == account.email)
                .where(Account.workspace_id.is_(None))
                .where(Account.workspace_label == account.workspace_label)
                .order_by(Account.created_at.asc(), Account.id.asc())
                .limit(1)
            )
            if matched := result.scalar_one_or_none():
                return matched
        if workspace_slot and account.email:
            column, value = workspace_slot
            result = await self._session.execute(
                select(Account)
                .where(Account.provider == account.provider)
                .where(Account.email == account.email)
                .where(column == value)
                .order_by(Account.created_at.asc(), Account.id.asc())
                .limit(1)
            )
            matched = result.scalar_one_or_none()
            if matched is not None and _can_reuse_email_fallback(matched, account):
                return matched
        return None

    def _dialect_name(self) -> str:
        return self._session.get_bind().dialect.name

    async def _acquire_sqlite_merge_lock(self) -> None:
        try:
            await self._session.execute(text("BEGIN IMMEDIATE"))
        except OperationalError as exc:
            message = str(exc).lower()
            if "within a transaction" not in message:
                raise
            await self._session.execute(text("UPDATE accounts SET id = id WHERE 1 = 0"))

    async def _acquire_postgresql_merge_lock(self, email: str) -> None:
        lock_key = advisory_lock_key("merge-email", email)
        await self._session.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})

    async def _acquire_postgresql_identity_lock(self, account_id: str) -> None:
        lock_key = advisory_lock_key("account-id", account_id)
        await self._session.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})
