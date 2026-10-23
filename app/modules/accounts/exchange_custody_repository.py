from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, func, insert, literal, or_, select, update

from app.core.config.settings import get_settings
from app.db.account_state_models import AccountExchangeIntent
from app.db.models import Account, AccountStatus
from app.modules.accounts.repository_state import EXCHANGE_RESET_REASONS, AutoResetResult, ExchangeIntentWindow


class AccountExchangeCustodyRepository:
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
        self, account_id: str, token_hash: str, *, timeout_seconds: float, base_seconds: float, max_attempts: int
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
            or (intent is None)
            or (intent.refresh_token_sha256 != token_hash)
            or (intent.reason is not None)
            or (not intent.replay)
        ):
            await self._session.commit()
            unsaved = intent is not None and intent.reason == "unsaved" and (intent.refresh_token_sha256 == token_hash)
            return AutoResetResult("unsaved" if unsaved else "ineligible", attempt, prior_replay, prior_reason)
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
                AccountExchangeIntent.account_id == account_id, AccountExchangeIntent.refresh_token_sha256 == stale_hash
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
                        .returning(AccountExchangeIntent.account_id)
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
                AccountExchangeIntent.account_id == account_id, AccountExchangeIntent.refresh_token_sha256 == token_hash
            )
        )
        await self._session.commit()

    async def clear_exchange_intent(self, account_id: str, timeout_seconds: float) -> bool | datetime:
        window = await self.exchange_intent_window(account_id, timeout_seconds)
        if window is None or window.reason == "unsaved":
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
