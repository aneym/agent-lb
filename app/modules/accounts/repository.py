from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account
from app.modules.accounts.credential_repository import AccountCredentialRepository as AccountCredentialRepository
from app.modules.accounts.delete_repository import AccountDeleteRepository as AccountDeleteRepository
from app.modules.accounts.exchange_custody_repository import (
    AccountExchangeCustodyRepository as AccountExchangeCustodyRepository,
)
from app.modules.accounts.identity_repository import AccountIdentityRepository
from app.modules.accounts.repository_state import (
    _HARD_STICKY_OUTAGE_GRACE_SEEDED_SENTINEL as _HARD_STICKY_OUTAGE_GRACE_SEEDED_SENTINEL,
)
from app.modules.accounts.repository_state import _HARD_STICKY_UNAVAILABLE_STATUSES as _HARD_STICKY_UNAVAILABLE_STATUSES
from app.modules.accounts.repository_state import _SUMMARY_CACHE_MAX_ENTRIES as _SUMMARY_CACHE_MAX_ENTRIES
from app.modules.accounts.repository_state import _SUMMARY_CACHE_TTL_SECONDS as _SUMMARY_CACHE_TTL_SECONDS
from app.modules.accounts.repository_state import _UNSET as _UNSET
from app.modules.accounts.repository_state import ACCOUNT_PENDING_DELETION_REASON as ACCOUNT_PENDING_DELETION_REASON
from app.modules.accounts.repository_state import EXCHANGE_RESET_REASONS as EXCHANGE_RESET_REASONS
from app.modules.accounts.repository_state import AccountIdentityRelockError as AccountIdentityRelockError
from app.modules.accounts.repository_state import AccountRequestUsageSummary as AccountRequestUsageSummary
from app.modules.accounts.repository_state import AutoResetResult as AutoResetResult
from app.modules.accounts.repository_state import ExchangeIntentWindow as ExchangeIntentWindow
from app.modules.accounts.repository_state import _apply_account_updates as _apply_account_updates
from app.modules.accounts.repository_state import _cached_request_usage_summaries as _cached_request_usage_summaries
from app.modules.accounts.repository_state import _can_reuse_email_fallback as _can_reuse_email_fallback
from app.modules.accounts.repository_state import (
    _clear_request_usage_summary_cache as _clear_request_usage_summary_cache,
)
from app.modules.accounts.repository_state import (
    _is_workspace_less_reauth_for_known_slot as _is_workspace_less_reauth_for_known_slot,
)
from app.modules.accounts.repository_state import _request_usage_summary_cache as _request_usage_summary_cache
from app.modules.accounts.repository_state import _same_unknown_workspace_identity as _same_unknown_workspace_identity
from app.modules.accounts.repository_state import _slot_lock_key as _slot_lock_key
from app.modules.accounts.repository_state import _store_request_usage_summaries as _store_request_usage_summaries
from app.modules.accounts.repository_state import _summary_cache_generation as _summary_cache_generation
from app.modules.accounts.repository_state import (
    _upsert_identity_candidate_predicates as _upsert_identity_candidate_predicates,
)
from app.modules.accounts.repository_state import _workspace_slot_identity as _workspace_slot_identity
from app.modules.accounts.repository_state import _workspace_slot_key as _workspace_slot_key
from app.modules.accounts.repository_state import credentials_replaced_since_wipe as credentials_replaced_since_wipe
from app.modules.accounts.slot_repository import AccountSlotRepository as AccountSlotRepository
from app.modules.accounts.state_repository import AccountStateRepository
from app.modules.accounts.usage_repository import AccountUsageRepository


class AccountsRepository(
    AccountExchangeCustodyRepository,
    AccountDeleteRepository,
    AccountCredentialRepository,
    AccountSlotRepository,
    AccountIdentityRepository,
    AccountUsageRepository,
    AccountStateRepository,
):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def get_by_id(self, account_id: str) -> Account | None:
        return await self._session.get(Account, account_id)

    async def get_by_id_fresh(self, account_id: str) -> Account | None:
        result = await self._session.execute(
            select(Account).where(Account.id == account_id).execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def reload_by_id(self, account_id: str) -> Account | None:
        return await self._session.get(Account, account_id, populate_existing=True)

    async def list_accounts(self, *, refresh_existing: bool = False) -> list[Account]:
        stmt = select(Account).where(Account.delete_requested_at.is_(None)).order_by(Account.email)
        if refresh_existing:
            stmt = stmt.execution_options(populate_existing=True)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_accounts_by_ids(self, account_ids: list[str], *, refresh_existing: bool = False) -> list[Account]:
        if not account_ids:
            return []
        stmt = (
            select(Account)
            .where(Account.id.in_(account_ids))
            .where(Account.delete_requested_at.is_(None))
            .order_by(Account.email)
        )
        if refresh_existing:
            stmt = stmt.execution_options(populate_existing=True)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


from app.modules.accounts.usage_rollup import AccountUsageRollupRepository as AccountUsageRollupRepository
