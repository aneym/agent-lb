from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, insert, literal, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AccountLimitWarmup
from app.db.session import sqlite_writer_section

ANTHROPIC_CONTINUOUS_WINDOW = "anthropic_primary_continuous"


OPENAI_CONTINUOUS_WINDOW = "openai_weekly_continuous"


_MAX_CONTINUOUS_RETRIES = 5


class LimitWarmupRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def latest_by_account(self, account_ids: list[str]) -> dict[str, AccountLimitWarmup]:
        if not account_ids:
            return {}
        subq = (
            select(
                AccountLimitWarmup.id.label("warmup_id"),
                func.row_number()
                .over(
                    partition_by=AccountLimitWarmup.account_id,
                    order_by=(AccountLimitWarmup.attempted_at.desc(), AccountLimitWarmup.id.desc()),
                )
                .label("row_number"),
            )
            .where(AccountLimitWarmup.account_id.in_(account_ids))
            .subquery()
        )
        stmt = (
            select(AccountLimitWarmup)
            .join(subq, AccountLimitWarmup.id == subq.c.warmup_id)
            .where(subq.c.row_number == 1)
        )
        result = await self._session.execute(stmt)
        return {entry.account_id: entry for entry in result.scalars().all()}

    async def last_primed_by_account(self, account_ids: list[str]) -> dict[str, datetime]:
        if not account_ids:
            return {}
        stmt = (
            select(AccountLimitWarmup.account_id, func.max(AccountLimitWarmup.completed_at))
            .where(
                AccountLimitWarmup.account_id.in_(account_ids),
                AccountLimitWarmup.window.in_((ANTHROPIC_CONTINUOUS_WINDOW, OPENAI_CONTINUOUS_WINDOW)),
                AccountLimitWarmup.status == "succeeded",
            )
            .group_by(AccountLimitWarmup.account_id)
        )
        return {account_id: completed_at for account_id, completed_at in (await self._session.execute(stmt)).all()}

    async def latest_attempt_for_account(self, account_id: str) -> AccountLimitWarmup | None:
        stmt = (
            select(AccountLimitWarmup)
            .where(AccountLimitWarmup.account_id == account_id)
            .order_by(AccountLimitWarmup.attempted_at.desc(), AccountLimitWarmup.id.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def claim_continuous_attempt(
        self,
        *,
        account_id: str,
        reset_at: int,
        model: str,
        now: datetime,
        window: str = ANTHROPIC_CONTINUOUS_WINDOW,
    ) -> AccountLimitWarmup | None:

        existing = await self._existing_attempt(account_id=account_id, window=window, reset_at=reset_at)
        if existing is None:
            return await self.try_create_attempt(
                account_id=account_id, window=window, reset_at=reset_at, model=model, attempted_at=now
            )
        if existing.retry_count >= _MAX_CONTINUOUS_RETRIES:
            return None
        delay = min(30 * 2 ** min(existing.retry_count, 5), 900)
        if existing.status != "failed" or now - existing.attempted_at < timedelta(seconds=delay):
            return None
        stmt = (
            update(AccountLimitWarmup)
            .where(AccountLimitWarmup.id == existing.id, AccountLimitWarmup.status == "failed")
            .values(
                status="pending",
                attempted_at=now,
                completed_at=None,
                error_code=None,
                error_message=None,
                retry_count=AccountLimitWarmup.retry_count + 1,
            )
            .returning(AccountLimitWarmup.id)
        )
        async with sqlite_writer_section(self._session):
            result = await self._session.execute(stmt)
            await self._session.commit()
        if result.scalar_one_or_none() is None:
            return None
        await self._session.refresh(existing)
        return existing

    async def try_create_attempt(
        self,
        *,
        account_id: str,
        window: str,
        reset_at: int,
        model: str,
        attempted_at: datetime,
        status: str = "pending",
        reset_at_tolerance_seconds: int = 0,
    ) -> AccountLimitWarmup | None:
        tolerance = max(0, reset_at_tolerance_seconds)
        table = AccountLimitWarmup.__table__
        # Single atomic conditional insert: the tolerance-window existence guard
        # and the insert execute as one statement, so the dedup is effective
        # across processes and replicas. On SQLite the statement is atomic under
        # the database-level single-writer lock (the process-local
        # sqlite_writer_section is kept only as a local write throttle). On
        # PostgreSQL a single INSERT .. SELECT is not self-sufficient under READ
        # COMMITTED, so an advisory transaction lock keyed on (account, window)
        # serializes concurrent attempts first.
        duplicate_in_tolerance_window = (
            select(AccountLimitWarmup.id)
            .where(
                AccountLimitWarmup.account_id == account_id,
                AccountLimitWarmup.window == window,
                AccountLimitWarmup.reset_at.between(reset_at - tolerance, reset_at + tolerance),
            )
            .exists()
        )
        insert_stmt = (
            insert(AccountLimitWarmup)
            .from_select(
                ["account_id", "window", "reset_at", "status", "model", "attempted_at"],
                select(
                    literal(account_id, type_=table.c.account_id.type),
                    literal(window, type_=table.c.window.type),
                    literal(reset_at, type_=table.c.reset_at.type),
                    literal(status, type_=table.c.status.type),
                    literal(model, type_=table.c.model.type),
                    literal(attempted_at, type_=table.c.attempted_at.type),
                ).where(~duplicate_in_tolerance_window),
            )
            .returning(AccountLimitWarmup.id)
        )
        try:
            async with sqlite_writer_section():
                if self._dialect_name() == "postgresql":
                    await self._session.execute(
                        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                        {"key": f"limit_warmup:{account_id}:{window}"},
                    )
                inserted_id = await self._session.scalar(insert_stmt)
                await self._session.commit()
        except IntegrityError:
            # Backstop: the exact-tuple unique constraint
            # uq_account_limit_warmups_account_window_reset still catches any
            # duplicate the guard could not see.
            await self._session.rollback()
            return None
        if inserted_id is None:
            return None
        row = await self._session.get(AccountLimitWarmup, inserted_id)
        if row is not None:
            await self._session.refresh(row)
        return row

    async def complete_attempt(
        self,
        attempt_id: int,
        *,
        status: str,
        completed_at: datetime,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AccountLimitWarmup | None:
        stmt = (
            update(AccountLimitWarmup)
            .where(AccountLimitWarmup.id == attempt_id)
            .values(
                status=status,
                completed_at=completed_at,
                error_code=error_code,
                error_message=error_message,
                updated_at=completed_at,
            )
            .returning(AccountLimitWarmup.id)
        )
        async with sqlite_writer_section(self._session):
            result = await self._session.execute(stmt)
            await self._session.commit()
        if result.scalar_one_or_none() is None:
            return None
        row = await self._session.get(AccountLimitWarmup, attempt_id)
        if row is not None:
            await self._session.refresh(row)
        return row

    def _dialect_name(self) -> str:
        bind = self._session.get_bind()
        return bind.dialect.name if bind else "sqlite"
