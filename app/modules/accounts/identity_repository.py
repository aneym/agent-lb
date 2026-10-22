from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import delete, or_, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.providers import normalize_provider_name
from app.db.models import Account, AccountLimitWarmup, AccountStatus, AdditionalUsageHistory, ApiKeyAccountAssignment, DashboardSettings, HttpBridgeSessionRecord, RequestLog, StickySession, UsageHistory  # noqa: I001  # fmt: skip

_SETTINGS_ROW_ID = 1
_DUPLICATE_ACCOUNT_SUFFIX = "__copy"


class AccountIdentityConflictError(Exception):
    def __init__(self, email: str) -> None:
        self.email = email
        super().__init__(
            f"Cannot overwrite account for email '{email}' because multiple matching accounts exist. "
            "Remove duplicates or enable import without overwrite."
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
        self,
        account: Account,
        *,
        merge_by_email: bool | None = None,
        merge_by_chatgpt_identity: bool = False,
    ) -> Account:
        account.provider = normalize_provider_name(account.provider)
        dialect_name = self._dialect_name()
        sqlite_lock_acquired = False
        if merge_by_email is None:
            if dialect_name == "sqlite":
                await self._acquire_sqlite_merge_lock()
                sqlite_lock_acquired = True
            merge_by_email = await self._merge_by_email_enabled()

        if dialect_name == "sqlite" and not sqlite_lock_acquired:
            await self._acquire_sqlite_merge_lock()
        elif dialect_name == "postgresql":
            identity_locked = False
            if merge_by_chatgpt_identity and account.chatgpt_account_id:
                await self._acquire_postgresql_identity_lock(f"chatgpt:{account.chatgpt_account_id}")
                identity_locked = True
            if merge_by_email:
                await self._acquire_postgresql_merge_lock(account.email)
            elif not identity_locked:
                await self._acquire_postgresql_identity_lock(account.id)

        if merge_by_chatgpt_identity and account.chatgpt_account_id:
            canonical = await self._account_by_chatgpt_identity(
                account.chatgpt_account_id,
                workspace_id=account.workspace_id,
            )
            if canonical is not None:
                _apply_account_updates(canonical, account)
                await self._clear_exchange_intent_for_reauth(canonical.id)
                await self._reconcile_chatgpt_identity_duplicates(
                    canonical=canonical,
                    chatgpt_account_id=account.chatgpt_account_id,
                    workspace_id=account.workspace_id,
                )
                await self._session.commit()
                await self._session.refresh(canonical)
                return canonical

        existing = await self._session.get(Account, account.id)
        if existing:
            if merge_by_email or _is_workspace_less_reauth_for_known_slot(
                existing,
                account,
                merge_by_chatgpt_identity=merge_by_chatgpt_identity,
            ):
                _apply_account_updates(existing, account)
                await self._clear_exchange_intent_for_reauth(existing.id)
                await self._session.commit()
                await self._session.refresh(existing)
                return existing
            account.id = await self._next_available_account_id(account.id)

        if merge_by_email:
            existing_by_email = await self._single_account_by_email(account.email)
            if existing_by_email:
                _apply_account_updates(existing_by_email, account)
                await self._clear_exchange_intent_for_reauth(existing_by_email.id)
                await self._session.commit()
                await self._session.refresh(existing_by_email)
                return existing_by_email

        self._session.add(account)
        await self._session.commit()
        await self._session.refresh(account)
        return account

    async def upsert_reauthorized(self, account: Account) -> Account:
        if account.chatgpt_account_id:
            return await self.upsert(account, merge_by_email=False, merge_by_chatgpt_identity=True)
        return await self.upsert_account_slot(account, preserve_unknown_workspace_duplicates=False)

    async def upsert_account_slot(
        self,
        account: Account,
        *,
        preserve_unknown_workspace_duplicates: bool | None = None,
    ) -> Account:
        if preserve_unknown_workspace_duplicates is None:
            preserve_unknown_workspace_duplicates = not await self._merge_by_email_enabled()
        dialect_name = self._dialect_name()
        if dialect_name == "sqlite":
            await self._acquire_sqlite_merge_lock()
        elif dialect_name == "postgresql":
            for lock_key in sorted(
                _slot_lock_keys(
                    account,
                    preserve_unknown_workspace_duplicates=preserve_unknown_workspace_duplicates,
                )
            ):
                await self._acquire_postgresql_identity_lock(lock_key)

        existing = await self._account_by_slot_identity(account)
        if existing:
            _apply_account_updates(existing, account)
            await self._clear_exchange_intent_for_reauth(existing.id)
            await self._session.commit()
            await self._session.refresh(existing)
            return existing

        existing_by_id = await self._session.get(Account, account.id)
        if existing_by_id:
            if _same_unknown_workspace_identity(existing_by_id, account) and not preserve_unknown_workspace_duplicates:
                _apply_account_updates(existing_by_id, account)
                await self._clear_exchange_intent_for_reauth(existing_by_id.id)
                await self._session.commit()
                await self._session.refresh(existing_by_id)
                return existing_by_id
            account.id = await self._next_available_account_id(account.id)
        elif not preserve_unknown_workspace_duplicates:
            existing_by_email = (
                await self._single_unknown_workspace_account_by_email(account.email)
                if account.workspace_id
                else await self._single_account_by_email(account.email)
                if not account.workspace_id
                else None
            )
            if existing_by_email and not _can_reuse_email_fallback(existing_by_email, account):
                existing_by_email = None
            if existing_by_email:
                _apply_account_updates(existing_by_email, account)
                await self._clear_exchange_intent_for_reauth(existing_by_email.id)
                await self._session.commit()
                await self._session.refresh(existing_by_email)
                return existing_by_email

        self._session.add(account)
        await self._session.commit()
        await self._session.refresh(account)
        return account

    async def _account_by_chatgpt_identity(
        self,
        chatgpt_account_id: str,
        *,
        workspace_id: str | None,
    ) -> Account | None:

        stmt = select(Account).where(Account.chatgpt_account_id == chatgpt_account_id)
        order_by: list[Any] = [Account.created_at.asc(), Account.id.asc()]
        if workspace_id:
            stmt = stmt.where(or_(Account.workspace_id == workspace_id, Account.workspace_id.is_(None)))
            order_by.insert(0, Account.workspace_id.is_(None).asc())
        else:
            stmt = stmt.where(Account.workspace_id.is_(None))

        result = await self._session.execute(stmt.order_by(*order_by).limit(1))
        return result.scalar_one_or_none()

    async def _reconcile_chatgpt_identity_duplicates(
        self,
        canonical: Account,
        chatgpt_account_id: str,
        workspace_id: str | None,
    ) -> None:
        duplicate_stmt = select(Account.id).where(
            Account.chatgpt_account_id == chatgpt_account_id,
            Account.id != canonical.id,
        )
        if workspace_id is None:
            duplicate_stmt = duplicate_stmt.where(Account.workspace_id.is_(None))
        else:
            duplicate_stmt = duplicate_stmt.where(Account.workspace_id == workspace_id)
        duplicate_accounts = (await self._session.execute(duplicate_stmt)).scalars().all()
        duplicate_ids = list(duplicate_accounts)
        if not duplicate_ids:
            return

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
        await self._session.execute(delete(Account).where(Account.id.in_(duplicate_ids)))

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

    async def _single_account_by_email(self, email: str) -> Account | None:
        result = await self._session.execute(
            select(Account).where(Account.email == email).order_by(Account.created_at.asc(), Account.id.asc()).limit(2)
        )
        matches = list(result.scalars().all())
        if not matches:
            return None
        if len(matches) > 1:
            raise AccountIdentityConflictError(email)
        return matches[0]

    async def _single_unknown_workspace_account_by_email(self, email: str) -> Account | None:
        result = await self._session.execute(
            select(Account)
            .where(Account.email == email)
            .where(Account.workspace_id.is_(None))
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
        if account.chatgpt_account_id and account.workspace_id:
            result = await self._session.execute(
                select(Account)
                .where(Account.chatgpt_account_id == account.chatgpt_account_id)
                .where(Account.workspace_id == account.workspace_id)
                .order_by(Account.created_at.asc(), Account.id.asc())
                .limit(1)
            )
            if matched := result.scalar_one_or_none():
                return matched
        if account.workspace_id and account.email:
            result = await self._session.execute(
                select(Account)
                .where(Account.email == account.email)
                .where(Account.workspace_id == account.workspace_id)
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
        lock_key = _advisory_lock_key("merge-email", email)
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )

    async def _acquire_postgresql_identity_lock(self, account_id: str) -> None:
        lock_key = _advisory_lock_key("account-id", account_id)
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )


def _apply_account_updates(target: Account, source: Account) -> None:
    target.provider = normalize_provider_name(source.provider)
    if source.chatgpt_account_id is not None:
        target.chatgpt_account_id = source.chatgpt_account_id
    target.email = source.email
    if source.workspace_id is not None or target.workspace_id is None:
        target.workspace_id = source.workspace_id
        target.workspace_label = source.workspace_label
        target.seat_type = source.seat_type
    target.plan_type = source.plan_type
    target.access_token_encrypted = source.access_token_encrypted
    target.refresh_token_encrypted = source.refresh_token_encrypted
    target.id_token_encrypted = source.id_token_encrypted
    target.last_refresh = source.last_refresh
    target.access_expires_at = source.access_expires_at
    target.status = source.status
    target.deactivation_reason = source.deactivation_reason
    target.reset_at = source.reset_at
    target.blocked_at = source.blocked_at


def _slot_lock_keys(account: Account, *, preserve_unknown_workspace_duplicates: bool = True) -> tuple[str, ...]:
    keys: list[str] = []
    if account.chatgpt_account_id and account.workspace_id:
        keys.append(f"slot:{account.chatgpt_account_id}:{account.workspace_id}")
    if account.email and account.workspace_id:
        keys.append(f"slot-email:{account.email}:{account.workspace_id}")
        if not preserve_unknown_workspace_duplicates:
            keys.append(f"slot-email-unknown:{account.email}")
    if keys:
        return tuple(keys)
    if account.email and not preserve_unknown_workspace_duplicates:
        return (f"slot-email-unknown:{account.email}",)
    return (f"slot-local:{account.id}",)


def _same_unknown_workspace_identity(existing: Account, incoming: Account) -> bool:
    return (
        not existing.workspace_id
        and not incoming.workspace_id
        and existing.chatgpt_account_id == incoming.chatgpt_account_id
        and existing.email == incoming.email
    )


def _is_workspace_less_reauth_for_known_slot(
    existing: Account,
    incoming: Account,
    *,
    merge_by_chatgpt_identity: bool,
) -> bool:
    return (
        merge_by_chatgpt_identity
        and existing.workspace_id is not None
        and incoming.workspace_id is None
        and incoming.chatgpt_account_id is not None
        and existing.chatgpt_account_id == incoming.chatgpt_account_id
        and existing.email == incoming.email
    )


def _can_reuse_email_fallback(existing: Account, incoming: Account) -> bool:
    return (
        not incoming.chatgpt_account_id
        or not existing.chatgpt_account_id
        or existing.chatgpt_account_id == incoming.chatgpt_account_id
    )


def _advisory_lock_key(scope: str, value: str) -> int:
    digest = hashlib.sha256(f"{scope}:{value}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)
