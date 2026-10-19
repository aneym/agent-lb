from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import delete, func, insert, literal, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.settings import get_settings
from app.core.utils.time import utcnow
from app.db.models import (
    Account,
    AccountExchangeIntent,
    AccountStatus,
    RequestLog,
    StickySession,
    UsageHistory,
)
from app.modules.accounts.identity_repository import AccountIdentityRepository, _slot_lock_keys
from app.modules.accounts.state_repository import AccountStateRepository
from app.modules.accounts.usage_repository import AccountUsageRepository

EXCHANGE_RESET_REASONS = frozenset({"operator_reset", "auto_reset"})


@dataclass(frozen=True)
class AutoResetResult:
    outcome: Literal["reset", "waiting", "exhausted", "ineligible"]
    attempt: int
    prior_replay: bool
    prior_reason: str | None


_UNSET = object()


@dataclass(frozen=True, slots=True)
class ExchangeIntentWindow:
    token_hash: str
    replay: bool
    reason: str | None
    available_at: datetime
    ready: bool


class AccountsRepository(AccountIdentityRepository, AccountUsageRepository, AccountStateRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def get_by_id(self, account_id: str) -> Account | None:
        return await self._session.get(Account, account_id)

    async def reload_by_id(self, account_id: str) -> Account | None:
        return await self._session.get(Account, account_id, populate_existing=True)

    async def list_accounts(self, *, refresh_existing: bool = False) -> list[Account]:
        stmt = select(Account).order_by(Account.email)
        if refresh_existing:
            stmt = stmt.execution_options(populate_existing=True)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def delete(self, account_id: str, *, delete_history: bool = False) -> bool:
        await self._session.execute(delete(UsageHistory).where(UsageHistory.account_id == account_id))
        if delete_history:
            await self._session.execute(delete(RequestLog).where(RequestLog.account_id == account_id))
        else:
            await self._session.execute(
                update(RequestLog)
                .where(RequestLog.account_id == account_id)
                .values(account_id=None, deleted_at=utcnow()),
            )
        await self._session.execute(delete(StickySession).where(StickySession.account_id == account_id))
        result = await self._session.execute(delete(Account).where(Account.id == account_id).returning(Account.id))
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

    async def exchange_intent_window(self, account_id: str, timeout_seconds: float) -> ExchangeIntentWindow | None:
        row = (
            await self._session.execute(
                select(
                    AccountExchangeIntent.refresh_token_sha256,
                    AccountExchangeIntent.replay,
                    AccountExchangeIntent.reason,
                    AccountExchangeIntent.started_at,
                    func.timezone("UTC", func.clock_timestamp())
                    if self._session.bind.dialect.name == "postgresql"
                    else func.current_timestamp(),
                ).where(AccountExchangeIntent.account_id == account_id)
            )
        ).one_or_none()
        if row is None:
            return None
        token_hash, replay, reason, started_at, database_now = row
        available_at = started_at + timedelta(seconds=timeout_seconds + 900)
        if available_at.tzinfo is not None and database_now.tzinfo is None:
            available_at = available_at.replace(tzinfo=None)
        if database_now.tzinfo is not None and available_at.tzinfo is None:
            database_now = database_now.replace(tzinfo=None)
        return ExchangeIntentWindow(token_hash, replay, reason, available_at, database_now >= available_at)

    async def auto_reset_exchange_intent(
        self,
        account_id: str,
        token_hash: str,
        *,
        timeout_seconds: float,
        base_seconds: float,
        max_attempts: int,
    ) -> AutoResetResult:
        account = (
            await self._session.execute(
                select(Account)
                .where(Account.id == account_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        intent = (
            await self._session.execute(
                select(AccountExchangeIntent)
                .where(AccountExchangeIntent.account_id == account_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        attempt = intent.auto_resets if intent is not None else 0
        prior_replay = intent.replay if intent is not None else False
        prior_reason = intent.reason if intent is not None else None
        if (
            account is None
            or account.status != AccountStatus.EXCHANGE_UNCERTAIN
            or account.owner_instance not in (None, get_settings().local_instance_id)
            or intent is None
            or intent.refresh_token_sha256 != token_hash
            or intent.reason not in (None, "unsaved")
            or not (intent.replay or intent.reason == "unsaved")
        ):
            await self._session.commit()
            return AutoResetResult("ineligible", attempt, prior_replay, prior_reason)
        if attempt >= max_attempts:
            intent.reason = "auto_exhausted"
            await self._session.commit()
            return AutoResetResult("exhausted", attempt, prior_replay, prior_reason)
        database_now = (
            await self._session.execute(
                select(
                    func.timezone("UTC", func.clock_timestamp())
                    if self._session.bind.dialect.name == "postgresql"
                    else func.current_timestamp()
                )
            )
        ).scalar_one()
        available_at = intent.started_at + timedelta(seconds=timeout_seconds + base_seconds * 2**attempt)
        if available_at.tzinfo is not None and database_now.tzinfo is None:
            available_at = available_at.replace(tzinfo=None)
        if database_now.tzinfo is not None and available_at.tzinfo is None:
            database_now = database_now.replace(tzinfo=None)
        if database_now < available_at:
            await self._session.commit()
            return AutoResetResult("waiting", attempt, prior_replay, prior_reason)
        intent.reason = "auto_reset"
        intent.auto_resets = attempt + 1
        await self._session.commit()
        return AutoResetResult("reset", attempt + 1, prior_replay, prior_reason)

    async def exchange_intent_hash(self, account_id: str) -> str | None:
        return (
            await self._session.execute(
                select(AccountExchangeIntent.refresh_token_sha256).where(AccountExchangeIntent.account_id == account_id)
            )
        ).scalar_one_or_none()

    async def drop_stale_exchange_intent(self, account_id: str, stale_hash: str, expected_token: bytes) -> bool:

        account = (
            await self._session.execute(
                select(Account.refresh_token_encrypted, Account.status)
                .where(Account.id == account_id)
                .with_for_update()
            )
        ).one_or_none()
        if (
            account is None
            or account.refresh_token_encrypted != expected_token
            or account.status == AccountStatus.EXCHANGE_UNCERTAIN
        ):
            await self._session.rollback()
            return False
        result = await self._session.execute(
            delete(AccountExchangeIntent).where(
                AccountExchangeIntent.account_id == account_id,
                AccountExchangeIntent.refresh_token_sha256 == stale_hash,
            )
        )
        await self._session.commit()
        return result.rowcount == 1

    async def begin_exchange(
        self,
        account_id: str,
        token_hash: str,
        expected_token: bytes,
        *,
        replay: bool = False,
        timeout_seconds: float | None = None,
    ) -> bool:

        from sqlalchemy.exc import IntegrityError

        carried = 0
        source = (
            select(
                Account.id,
                literal(token_hash),
                func.timezone("UTC", func.clock_timestamp())
                if self._session.bind.dialect.name == "postgresql"
                else func.current_timestamp(),
                literal(replay),
            )
            .where(
                Account.id == account_id,
                Account.refresh_token_encrypted == expected_token,
                or_(Account.owner_instance.is_(None), Account.owner_instance == get_settings().local_instance_id),
                or_(Account.status != AccountStatus.EXCHANGE_UNCERTAIN, literal(replay)),
                or_(
                    literal(not replay),
                    Account.status.not_in((AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED)),
                ),
            )
            .with_for_update()
        )
        try:
            async with self._session.begin_nested():
                eligible = not replay or timeout_seconds is not None
                if replay:
                    account_row = await self._session.execute(
                        select(Account.id)
                        .where(
                            Account.id == account_id,
                            Account.refresh_token_encrypted == expected_token,
                            Account.status.not_in((AccountStatus.REAUTH_REQUIRED, AccountStatus.DEACTIVATED)),
                            or_(
                                Account.owner_instance.is_(None),
                                Account.owner_instance == get_settings().local_instance_id,
                            ),
                        )
                        .with_for_update()
                    )
                    eligible = account_row.scalar_one_or_none() is not None
                    if eligible and timeout_seconds is not None:
                        window = await self.exchange_intent_window(account_id, timeout_seconds)
                        eligible = window is not None and window.token_hash == token_hash and window.ready
                        if eligible and window.reason not in EXCHANGE_RESET_REASONS:
                            eligible = not window.replay and window.reason not in {"unsaved", "auto_exhausted"}
                    if eligible:
                        carried = (
                            await self._session.execute(
                                select(AccountExchangeIntent.auto_resets)
                                .where(AccountExchangeIntent.account_id == account_id)
                                .with_for_update()
                            )
                        ).scalar_one()
                        retired = await self._session.execute(
                            delete(AccountExchangeIntent).where(
                                AccountExchangeIntent.account_id == account_id,
                                AccountExchangeIntent.refresh_token_sha256 == token_hash,
                            )
                        )
                        eligible = retired.rowcount == 1
                inserted = False
                if eligible:
                    result = await self._session.execute(
                        insert(AccountExchangeIntent)
                        .from_select(
                            ("account_id", "refresh_token_sha256", "started_at", "replay", "auto_resets"),
                            source.add_columns(literal(carried)),
                        )
                        .returning(AccountExchangeIntent.account_id),
                    )
                    inserted = result.scalar_one_or_none() is not None
        except IntegrityError:
            await self._session.rollback()
            return False
        if not inserted:
            await self._session.rollback()
            return False
        await self._session.commit()
        return True

    async def abort_exchange(self, account_id: str, token_hash: str) -> None:

        await self._session.execute(
            delete(AccountExchangeIntent).where(
                AccountExchangeIntent.account_id == account_id,
                AccountExchangeIntent.refresh_token_sha256 == token_hash,
            )
        )
        await self._session.commit()

    async def clear_exchange_intent(self, account_id: str, timeout_seconds: float) -> bool | datetime:

        window = await self.exchange_intent_window(account_id, timeout_seconds)
        if window is None:
            return False
        if not window.ready:
            return window.available_at
        result = await self._session.execute(
            update(Account)
            .where(Account.id == account_id, Account.status == AccountStatus.EXCHANGE_UNCERTAIN)
            .values(status=AccountStatus.ACTIVE, deactivation_reason=None)
            .returning(Account.id)
        )
        if result.scalar_one_or_none() is None:
            await self._session.rollback()
            return False
        await self._session.execute(
            update(AccountExchangeIntent)
            .where(AccountExchangeIntent.account_id == account_id)
            .values(reason="operator_reset", auto_resets=0)
        )
        await self._session.commit()
        return True

    async def _clear_exchange_intent_for_reauth(self, account_id: str) -> None:
        await self._session.execute(delete(AccountExchangeIntent).where(AccountExchangeIntent.account_id == account_id))

    async def mark_exchange_uncertain(
        self, account_id: str, token_hash: str, expected_token: bytes, *, reason: str | None = None
    ) -> None:
        result = await self._session.execute(
            update(Account)
            .where(Account.id == account_id)
            .where(
                Account.id.in_(
                    select(AccountExchangeIntent.account_id).where(
                        AccountExchangeIntent.account_id == account_id,
                        AccountExchangeIntent.refresh_token_sha256 == token_hash,
                    )
                )
            )
            .where(Account.refresh_token_encrypted == expected_token)
            .values(status=AccountStatus.EXCHANGE_UNCERTAIN, deactivation_reason="Refresh exchange outcome uncertain")
            .returning(Account.id)
        )
        if result.scalar_one_or_none() is not None and reason is not None:
            await self._session.execute(
                update(AccountExchangeIntent)
                .where(
                    AccountExchangeIntent.account_id == account_id,
                    AccountExchangeIntent.refresh_token_sha256 == token_hash,
                )
                .values(reason=reason)
            )
        await self._session.commit()

    async def workspace_slot_taken(
        self,
        *,
        account_id: str,
        email: str,
        chatgpt_account_id: str | None,
        workspace_id: str,
    ) -> bool:
        if chatgpt_account_id:
            predicates = [
                (Account.chatgpt_account_id == chatgpt_account_id) & (Account.workspace_id == workspace_id),
                (
                    (Account.email == email)
                    & (Account.workspace_id == workspace_id)
                    & Account.chatgpt_account_id.is_(None)
                ),
            ]
        else:
            predicates = [(Account.email == email) & (Account.workspace_id == workspace_id)]
        result = await self._session.execute(
            select(Account.id).where(Account.id != account_id).where(or_(*predicates)).limit(1)
        )
        return result.scalar_one_or_none() is not None


def _slot_lock_key(account: Account, *, preserve_unknown_workspace_duplicates: bool = True) -> str:
    return _slot_lock_keys(
        account,
        preserve_unknown_workspace_duplicates=preserve_unknown_workspace_duplicates,
    )[0]
