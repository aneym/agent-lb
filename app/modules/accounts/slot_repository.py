from __future__ import annotations

from sqlalchemy import or_, select

from app.core.upstream_proxy.cache import get_upstream_route_cache
from app.db.account_identity_lock import lock_postgresql_account_identities
from app.db.models import Account
from app.db.session import sqlite_writer_section
from app.modules.accounts.identity_repository import _slot_lock_keys
from app.modules.accounts.repository_state import (
    AccountIdentityRelockError,
    _apply_account_updates,
    _can_reuse_email_fallback,
    _clear_request_usage_summary_cache,
    _is_workspace_less_reauth_for_known_slot,
    _same_unknown_workspace_identity,
    _upsert_identity_candidate_predicates,
    _workspace_slot_key,
)
from app.modules.usage.plan_downgrade_observations import discard_plan_downgrade_observations
from app.modules.usage.repository import _clear_bulk_history_since_sqlite_cache


class AccountSlotRepository:
    async def _upsert_unlocked(
        self,
        account: Account,
        *,
        merge_by_email: bool | None = None,
        merge_by_chatgpt_identity: bool = False,
        _identity_lock_attempt: int = 0,
    ) -> Account:
        dialect_name = self._dialect_name()
        sqlite_lock_acquired = False
        if merge_by_email is None:
            if dialect_name == "sqlite":
                await self._acquire_sqlite_merge_lock()
                sqlite_lock_acquired = True
            merge_by_email = await self._merge_by_email_enabled()
        if dialect_name == "sqlite" and (not sqlite_lock_acquired):
            await self._acquire_sqlite_merge_lock()
        elif dialect_name == "postgresql":
            locked_identities = await self._lock_postgresql_upsert_identity_candidates(
                account, include_email=bool(merge_by_email)
            )
            if merge_by_email:
                await self._acquire_postgresql_merge_lock(account.email)
            elif not locked_identities:
                await self._acquire_postgresql_identity_lock(account.id)
            if not await self._postgresql_upsert_identity_candidates_are_locked(
                account, include_email=bool(merge_by_email), locked_identities=locked_identities
            ):
                await self._session.rollback()
                if _identity_lock_attempt >= 1:
                    raise AccountIdentityRelockError(
                        "Account identity candidates changed during PostgreSQL upsert locking"
                    )
                return await self._upsert_unlocked(
                    account,
                    merge_by_email=merge_by_email,
                    merge_by_chatgpt_identity=merge_by_chatgpt_identity,
                    _identity_lock_attempt=_identity_lock_attempt + 1,
                )
        if merge_by_chatgpt_identity and account.chatgpt_account_id:
            canonical = await self._account_by_chatgpt_identity(
                account.chatgpt_account_id,
                workspace_id=account.workspace_id,
                email=account.email,
                provider=account.provider,
            )
            if canonical is not None:
                await self._apply_account_replacement(canonical, account)
                usage_cache_dirty = await self._reconcile_chatgpt_identity_duplicates(
                    canonical=canonical,
                    chatgpt_account_id=account.chatgpt_account_id,
                    workspace_id=account.workspace_id,
                    email=account.email,
                )
                await self._session.commit()
                if usage_cache_dirty:
                    _clear_bulk_history_since_sqlite_cache()
                    _clear_request_usage_summary_cache()
                    await get_upstream_route_cache().invalidate()
                await self._session.refresh(canonical)
                return canonical
        existing = await self._session.get(Account, account.id)
        if existing:
            if existing.provider == account.provider and (
                merge_by_email
                or _is_workspace_less_reauth_for_known_slot(
                    existing, account, merge_by_chatgpt_identity=merge_by_chatgpt_identity
                )
            ):
                await self._apply_account_replacement(existing, account)
                await self._session.commit()
                await self._session.refresh(existing)
                return existing
            account.id = await self._next_available_account_id(account.id)
        if merge_by_email:
            existing_by_email = await self._single_account_by_email(account.email, provider=account.provider)
            if existing_by_email:
                await self._apply_account_replacement(existing_by_email, account)
                await self._session.commit()
                await self._session.refresh(existing_by_email)
                return existing_by_email
        self._session.add(account)
        await self._session.commit()
        await self._session.refresh(account)
        return account

    async def replace_reauthorized(self, account_id: str, account: Account) -> Account | None:
        async with sqlite_writer_section():
            if self._dialect_name() == "postgresql":
                existing = await self._lock_postgresql_account_identity_membership(
                    account_id, account.chatgpt_account_id
                )
            else:
                existing = await self._session.get(Account, account_id)
            if existing is None or existing.provider != account.provider:
                return None
            await self._apply_account_replacement(existing, account)
            await self._session.commit()
            await self._session.refresh(existing)
            return existing

    async def _apply_account_replacement(self, target: Account, source: Account) -> None:
        _apply_account_updates(target, source)
        await self._clear_exchange_intent_for_reauth(target.id)
        await discard_plan_downgrade_observations(self._session, target.id)

    async def _upsert_account_slot_unlocked(
        self,
        account: Account,
        *,
        preserve_unknown_workspace_duplicates: bool | None = None,
        preserve_identity_slots: bool = False,
        _identity_lock_attempt: int = 0,
    ) -> Account:
        if preserve_unknown_workspace_duplicates is None:
            preserve_unknown_workspace_duplicates = not await self._merge_by_email_enabled()
        dialect_name = self._dialect_name()
        if dialect_name == "sqlite":
            await self._acquire_sqlite_merge_lock()
        elif dialect_name == "postgresql":
            locked_identities = await self._lock_postgresql_upsert_identity_candidates(account, include_email=True)
            for lock_key in sorted(
                _slot_lock_keys(account, preserve_unknown_workspace_duplicates=preserve_unknown_workspace_duplicates)
            ):
                await self._acquire_postgresql_identity_lock(lock_key)
            if not await self._postgresql_upsert_identity_candidates_are_locked(
                account, include_email=True, locked_identities=locked_identities
            ):
                await self._session.rollback()
                if _identity_lock_attempt >= 1:
                    raise AccountIdentityRelockError(
                        "Account identity candidates changed during PostgreSQL slot locking"
                    )
                return await self._upsert_account_slot_unlocked(
                    account,
                    preserve_unknown_workspace_duplicates=preserve_unknown_workspace_duplicates,
                    preserve_identity_slots=preserve_identity_slots,
                    _identity_lock_attempt=_identity_lock_attempt + 1,
                )
        existing = await self._account_by_slot_identity(account)
        if existing:
            await self._apply_account_replacement(existing, account)
            await self._session.commit()
            await self._session.refresh(existing)
            return existing
        existing_by_id = await self._session.get(Account, account.id)
        if existing_by_id:
            if _same_unknown_workspace_identity(existing_by_id, account) and (
                not preserve_unknown_workspace_duplicates
            ):
                await self._apply_account_replacement(existing_by_id, account)
                await self._session.commit()
                await self._session.refresh(existing_by_id)
                return existing_by_id
            account.id = await self._next_available_account_id(account.id)
        elif not preserve_unknown_workspace_duplicates:
            if _workspace_slot_key(account):
                existing_by_email = await self._single_unknown_workspace_account_by_email(
                    account.email, provider=account.provider
                )
            elif preserve_identity_slots and account.chatgpt_account_id:
                existing_by_email = None
            else:
                existing_by_email = await self._single_account_by_email(account.email, provider=account.provider)
            if existing_by_email and (not _can_reuse_email_fallback(existing_by_email, account)):
                existing_by_email = None
            if existing_by_email:
                await self._apply_account_replacement(existing_by_email, account)
                await self._session.commit()
                await self._session.refresh(existing_by_email)
                return existing_by_email
        self._session.add(account)
        await self._session.commit()
        await self._session.refresh(account)
        return account

    async def workspace_slot_taken(
        self, *, account_id: str, email: str, chatgpt_account_id: str | None, workspace_id: str
    ) -> bool:
        if chatgpt_account_id:
            predicates = [
                (Account.chatgpt_account_id == chatgpt_account_id) & (Account.workspace_id == workspace_id),
                (Account.email == email)
                & (Account.workspace_id == workspace_id)
                & Account.chatgpt_account_id.is_(None),
            ]
        else:
            predicates = [(Account.email == email) & (Account.workspace_id == workspace_id)]
        result = await self._session.execute(
            select(Account.id).where(Account.id != account_id).where(or_(*predicates)).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def _lock_postgresql_account_identity_membership(
        self, account_id: str, incoming_chatgpt_account_id: str | None, *, second_attempt: bool = False
    ) -> Account | None:
        observed_identity = await self._session.scalar(
            select(Account.chatgpt_account_id).where(Account.id == account_id)
        )
        await lock_postgresql_account_identities(self._session, (observed_identity, incoming_chatgpt_account_id))
        locked_account = await self._session.scalar(
            select(Account)
            .where(Account.id == account_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        locked_identity = locked_account.chatgpt_account_id if locked_account is not None else None
        if locked_identity == observed_identity:
            return locked_account
        await self._session.rollback()
        if second_attempt:
            raise AccountIdentityRelockError("Account identity changed during PostgreSQL membership lock acquisition")
        return await self._lock_postgresql_account_identity_membership(
            account_id, incoming_chatgpt_account_id, second_attempt=True
        )

    async def _lock_postgresql_upsert_identity_candidates(
        self, account: Account, *, include_email: bool
    ) -> frozenset[str]:
        predicates = _upsert_identity_candidate_predicates(account, include_email=include_email)
        observed = (
            (await self._session.execute(select(Account.chatgpt_account_id).where(or_(*predicates)))).scalars().all()
        )
        identities = frozenset((identity for identity in (*observed, account.chatgpt_account_id) if identity))
        await lock_postgresql_account_identities(self._session, identities)
        return identities

    async def _postgresql_upsert_identity_candidates_are_locked(
        self, account: Account, *, include_email: bool, locked_identities: frozenset[str]
    ) -> bool:
        predicates = _upsert_identity_candidate_predicates(account, include_email=include_email)
        current = (
            (
                await self._session.execute(
                    select(Account.chatgpt_account_id).where(or_(*predicates)).with_for_update(key_share=True)
                )
            )
            .scalars()
            .all()
        )
        return all((identity is None or identity in locked_identities for identity in current))
