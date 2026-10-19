from __future__ import annotations

from collections.abc import Collection
from datetime import datetime

from anyio import to_thread
from sqlalchemy import Integer, and_, delete, func, insert, literal, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.settings import get_settings
from app.core.usage.types import UsageAggregateRow
from app.core.utils.time import utcnow
from app.db.models import Account, AdditionalUsageHistory, UsageHistory
from app.db.session import sqlite_writer_section
from app.db.sqlite_utils import sqlite_db_path_from_url
from app.modules.usage.additional_quota_keys import (
    canonicalize_additional_quota_key,
)
from app.modules.usage.additional_usage_reads import (
    AdditionalUsageReads,
    _additional_quota_match_clause,
    _resolve_additional_quota_query_scope,
)
from app.modules.usage.history_trends import UsageHistoryTrends, _window_clause
from app.modules.usage.sqlite_history import (
    UsageHistorySnapshot,
    _bulk_history_since_sqlite,
    _latest_by_account_sqlite,
)

BULK_HISTORY_MAX_ROWS_PER_ACCOUNT = 720


def _resolve_additional_quota_key(
    *,
    quota_key: str | None = None,
    limit_name: str | None = None,
    metered_feature: str | None = None,
) -> str | None:
    candidate_limit_name = quota_key if quota_key is not None else limit_name
    if candidate_limit_name is None and metered_feature is None:
        return None
    return canonicalize_additional_quota_key(
        quota_key=quota_key,
        limit_name=candidate_limit_name,
        metered_feature=metered_feature,
    )


class UsageRepository(UsageHistoryTrends):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def latest_entry_for_account(
        self,
        account_id: str,
        *,
        window: str | None = None,
    ) -> UsageHistory | None:
        stmt = (
            select(UsageHistory)
            .where(UsageHistory.account_id == account_id)
            .where(_window_clause(window))
            .order_by(UsageHistory.recorded_at.desc(), UsageHistory.id.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

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
        credits_cap: float | None = None,
        credits_spent: float | None = None,
    ) -> UsageHistory:
        entry = UsageHistory(
            account_id=account_id,
            provider=provider,
            used_percent=used_percent,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            window=window,
            reset_at=reset_at,
            window_minutes=window_minutes,
            credits_has=credits_has,
            credits_unlimited=credits_unlimited,
            credits_balance=credits_balance,
            credits_cap=credits_cap,
            credits_spent=credits_spent,
            recorded_at=recorded_at or utcnow(),
        )
        self._session.add(entry)
        async with sqlite_writer_section(self._session):
            await self._session.commit()
            await self._session.refresh(entry)
        return entry

    async def aggregate_since(
        self,
        since: datetime,
        window: str | None = None,
    ) -> list[UsageAggregateRow]:
        conditions = [UsageHistory.recorded_at >= since]
        if window:
            conditions.append(_window_clause(window))
        stmt = (
            select(
                UsageHistory.account_id,
                func.avg(UsageHistory.used_percent).label("used_percent_avg"),
                func.sum(UsageHistory.input_tokens).label("input_tokens_sum"),
                func.sum(UsageHistory.output_tokens).label("output_tokens_sum"),
                func.count(UsageHistory.id).label("samples"),
                func.max(UsageHistory.recorded_at).label("last_recorded_at"),
                func.max(UsageHistory.reset_at).label("reset_at_max"),
                func.max(UsageHistory.window_minutes).label("window_minutes_max"),
            )
            .where(*conditions)
            .group_by(UsageHistory.account_id)
        )
        result = await self._session.execute(stmt)
        rows = result.all()
        return [
            UsageAggregateRow(
                account_id=row.account_id,
                used_percent_avg=float(row.used_percent_avg) if row.used_percent_avg is not None else None,
                input_tokens_sum=int(row.input_tokens_sum) if row.input_tokens_sum is not None else None,
                output_tokens_sum=int(row.output_tokens_sum) if row.output_tokens_sum is not None else None,
                samples=int(row.samples),
                last_recorded_at=row.last_recorded_at,
                reset_at_max=int(row.reset_at_max) if row.reset_at_max is not None else None,
                window_minutes_max=int(row.window_minutes_max) if row.window_minutes_max is not None else None,
            )
            for row in rows
        ]

    async def latest_by_account(
        self,
        window: str | None = None,
        *,
        account_ids: Collection[str] | None = None,
    ) -> dict[str, UsageHistory]:
        conditions = _window_clause(window)
        if account_ids is not None and not account_ids:
            return {}
        if account_ids is not None:
            conditions = and_(conditions, UsageHistory.account_id.in_(account_ids))
        bind = self._session.get_bind()
        dialect = bind.dialect.name if bind else "sqlite"
        sqlite_path = sqlite_db_path_from_url(get_settings().database_url) if dialect == "sqlite" else None
        if sqlite_path is not None:
            return await to_thread.run_sync(
                _latest_by_account_sqlite,
                str(sqlite_path),
                window,
                list(account_ids) if account_ids is not None else None,
            )
        if dialect == "postgresql":
            acct_stmt = select(Account.id)
            if account_ids is not None:
                acct_stmt = acct_stmt.where(Account.id.in_(account_ids))
            acct_subq = acct_stmt.subquery("accts")
            lateral = (
                select(UsageHistory.id)
                .where(
                    conditions,
                    UsageHistory.account_id == acct_subq.c.id,
                )
                .order_by(UsageHistory.recorded_at.desc(), UsageHistory.id.desc())
                .limit(1)
                .correlate(acct_subq)
                .lateral("latest")
            )
            id_query = (
                select(lateral.c.id).select_from(acct_subq.outerjoin(lateral, true())).where(lateral.c.id.is_not(None))
            )
            stmt = select(UsageHistory).where(UsageHistory.id.in_(id_query))
            result = await self._session.execute(stmt)
            return {entry.account_id: entry for entry in result.scalars().all()}

        acct_stmt = select(Account.id)
        if account_ids is not None:
            acct_stmt = acct_stmt.where(Account.id.in_(account_ids))
        acct_subq = acct_stmt.subquery("accts")
        latest_id = (
            select(UsageHistory.id)
            .where(
                conditions,
                UsageHistory.account_id == acct_subq.c.id,
            )
            .order_by(UsageHistory.recorded_at.desc(), UsageHistory.id.desc())
            .limit(1)
            .correlate(acct_subq)
            .scalar_subquery()
        )
        id_rows = select(latest_id.label("usage_id")).select_from(acct_subq).subquery("latest_ids")
        stmt = select(UsageHistory).join(id_rows, UsageHistory.id == id_rows.c.usage_id)
        result = await self._session.execute(stmt)
        return {entry.account_id: entry for entry in result.scalars().all()}

    async def history_since(
        self,
        account_id: str,
        window: str,
        since: datetime,
    ) -> list[UsageHistory]:
        stmt = (
            select(UsageHistory)
            .where(
                UsageHistory.account_id == account_id,
                _window_clause(window),
                UsageHistory.recorded_at >= since,
            )
            .order_by(UsageHistory.recorded_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def bulk_history_since(
        self,
        account_ids: list[str],
        window: str,
        since: datetime,
    ) -> dict[str, list[UsageHistorySnapshot]]:

        if not account_ids:
            return {}
        bind = self._session.get_bind()
        dialect = bind.dialect.name if bind else "sqlite"
        sqlite_path = sqlite_db_path_from_url(get_settings().database_url) if dialect == "sqlite" else None
        if sqlite_path is not None:
            return await to_thread.run_sync(
                _bulk_history_since_sqlite,
                str(sqlite_path),
                list(account_ids),
                window,
                since,
            )

        ranked = (
            select(
                UsageHistory.id,
                UsageHistory.account_id,
                UsageHistory.used_percent,
                UsageHistory.recorded_at,
                UsageHistory.reset_at,
                UsageHistory.window_minutes,
                func.row_number()
                .over(
                    partition_by=UsageHistory.account_id,
                    order_by=(UsageHistory.recorded_at.desc(), UsageHistory.id.desc()),
                )
                .label("rn"),
            )
            .where(
                UsageHistory.account_id.in_(account_ids),
                _window_clause(window),
                UsageHistory.recorded_at >= since,
            )
            .subquery("ranked_history")
        )
        stmt = (
            select(
                ranked.c.id,
                ranked.c.account_id,
                ranked.c.used_percent,
                ranked.c.recorded_at,
                ranked.c.reset_at,
                ranked.c.window_minutes,
            )
            .where(ranked.c.rn <= BULK_HISTORY_MAX_ROWS_PER_ACCOUNT)
            .order_by(ranked.c.account_id, ranked.c.recorded_at.asc())
        )
        result = await self._session.execute(stmt)
        grouped: dict[str, list[UsageHistorySnapshot]] = {}
        for row in result.all():
            snapshot = UsageHistorySnapshot(
                id=int(row.id),
                account_id=row.account_id,
                used_percent=float(row.used_percent),
                recorded_at=row.recorded_at,
                reset_at=float(row.reset_at) if row.reset_at is not None else None,
                window_minutes=int(row.window_minutes) if row.window_minutes is not None else None,
            )
            grouped.setdefault(snapshot.account_id, []).append(snapshot)
        return grouped

    async def latest_window_minutes(self, window: str) -> int | None:
        conditions = _window_clause(window)
        result = await self._session.execute(select(func.max(UsageHistory.window_minutes)).where(conditions))
        value = result.scalar_one_or_none()
        return int(value) if value is not None else None


class AdditionalUsageRepository(AdditionalUsageReads):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
    ) -> None:
        effective_quota_key = _resolve_additional_quota_key(
            quota_key=quota_key,
            limit_name=limit_name,
            metered_feature=metered_feature,
        )
        if effective_quota_key is None:
            raise ValueError("additional usage quota_key could not be determined")
        entry = AdditionalUsageHistory(
            account_id=account_id,
            quota_key=effective_quota_key,
            limit_name=limit_name,
            metered_feature=metered_feature,
            window=window,
            used_percent=used_percent,
            reset_at=reset_at,
            window_minutes=window_minutes,
            recorded_at=recorded_at or utcnow(),
        )
        self._session.add(entry)
        async with sqlite_writer_section(self._session):
            await self._session.commit()

    async def clear_cooldown_if_latest(
        self,
        account_id: str,
        quota_key: str,
        window: str,
        expected_latest_id: int,
        *,
        metered_feature: str,
    ) -> bool:

        scope = _resolve_additional_quota_query_scope(quota_key=quota_key)
        if scope is None:
            raise ValueError("additional usage quota_key could not be determined")
        latest_id = (
            select(AdditionalUsageHistory.id)
            .where(
                AdditionalUsageHistory.account_id == account_id,
                _additional_quota_match_clause(scope),
                AdditionalUsageHistory.window == window,
            )
            .order_by(
                AdditionalUsageHistory.recorded_at.desc(),
                AdditionalUsageHistory.used_percent.desc(),
                AdditionalUsageHistory.id.desc(),
            )
            .limit(1)
            .scalar_subquery()
        )
        values = select(
            literal(account_id),
            literal(quota_key),
            literal(quota_key),
            literal(metered_feature),
            literal(window),
            literal(0.0),
            literal(None, type_=Integer),
            literal(None, type_=Integer),
            literal(utcnow()),
        ).where(latest_id == expected_latest_id)
        statement = insert(AdditionalUsageHistory).from_select(
            [
                "account_id",
                "quota_key",
                "limit_name",
                "metered_feature",
                "window",
                "used_percent",
                "reset_at",
                "window_minutes",
                "recorded_at",
            ],
            values,
        )
        async with sqlite_writer_section(self._session):
            result = await self._session.execute(statement)
            await self._session.commit()
        return result.rowcount == 1

    async def delete_for_account(self, account_id: str) -> None:
        stmt = delete(AdditionalUsageHistory).where(AdditionalUsageHistory.account_id == account_id)
        async with sqlite_writer_section(self._session):
            await self._session.execute(stmt)
            await self._session.commit()

    async def delete_for_account_and_quota_key(self, account_id: str, quota_key: str) -> None:
        scope = _resolve_additional_quota_query_scope(quota_key=quota_key)
        if scope is None:
            raise ValueError("additional usage quota_key could not be determined")
        stmt = delete(AdditionalUsageHistory).where(
            AdditionalUsageHistory.account_id == account_id,
            _additional_quota_match_clause(scope),
        )
        async with sqlite_writer_section(self._session):
            await self._session.execute(stmt)
            await self._session.commit()

    async def delete_for_account_and_limit(self, account_id: str, limit_name: str) -> None:
        await self.delete_for_account_and_quota_key(account_id, limit_name)

    async def delete_for_account_quota_key_window(
        self,
        account_id: str,
        quota_key: str,
        window: str,
    ) -> None:
        scope = _resolve_additional_quota_query_scope(quota_key=quota_key)
        if scope is None:
            raise ValueError("additional usage quota_key could not be determined")
        stmt = delete(AdditionalUsageHistory).where(
            AdditionalUsageHistory.account_id == account_id,
            _additional_quota_match_clause(scope),
            AdditionalUsageHistory.window == window,
        )
        async with sqlite_writer_section(self._session):
            await self._session.execute(stmt)
            await self._session.commit()

    async def delete_for_account_limit_window(
        self,
        account_id: str,
        limit_name: str,
        window: str,
    ) -> None:
        await self.delete_for_account_quota_key_window(account_id, limit_name, window)
