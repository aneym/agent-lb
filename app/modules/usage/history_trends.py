from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, and_, cast, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.usage.types import UsageTrendBucket
from app.db.models import UsageHistory

_PRIMARY_WINDOW_LITERAL = literal_column("'primary'")


def _normalized_window_expr():
    return func.coalesce(UsageHistory.window, _PRIMARY_WINDOW_LITERAL)


def _window_clause(window: str | None):
    if not window or window == "primary":
        return _normalized_window_expr() == "primary"
    return UsageHistory.window == window


class UsageHistoryTrends:
    _session: AsyncSession

    async def trends_by_bucket(
        self,
        since: datetime,
        bucket_seconds: int = 21600,
        window: str | None = None,
        account_id: str | None = None,
    ) -> list[UsageTrendBucket]:
        bind = self._session.get_bind()
        dialect = bind.dialect.name if bind else "sqlite"
        if dialect == "postgresql":
            bucket_expr = func.floor(func.extract("epoch", UsageHistory.recorded_at) / bucket_seconds) * bucket_seconds
        else:
            epoch_col = cast(func.strftime("%s", UsageHistory.recorded_at), Integer)
            bucket_expr = cast(epoch_col / bucket_seconds, Integer) * bucket_seconds
        bucket_col = bucket_expr.label("bucket_epoch")

        conditions: list = [UsageHistory.recorded_at >= since]
        if window:
            conditions.append(_window_clause(window))
        if account_id:
            conditions.append(UsageHistory.account_id == account_id)

        window_expr = _normalized_window_expr()
        if dialect == "sqlite":
            base_rows = (
                select(
                    bucket_col,
                    UsageHistory.id.label("usage_id"),
                    UsageHistory.account_id.label("account_id"),
                    window_expr.label("window"),
                    UsageHistory.used_percent.label("used_percent"),
                    UsageHistory.recorded_at.label("recorded_at"),
                )
                .where(*conditions)
                .subquery()
            )

            aggregate_rows = (
                select(
                    base_rows.c.bucket_epoch,
                    base_rows.c.account_id,
                    base_rows.c.window,
                    func.avg(base_rows.c.used_percent).label("avg_used_percent"),
                    func.count(base_rows.c.usage_id).label("samples"),
                    func.max(base_rows.c.recorded_at).label("max_recorded_at"),
                )
                .group_by(
                    base_rows.c.bucket_epoch,
                    base_rows.c.account_id,
                    base_rows.c.window,
                )
                .subquery()
            )

            latest_ids = (
                select(
                    aggregate_rows.c.bucket_epoch,
                    aggregate_rows.c.account_id,
                    aggregate_rows.c.window,
                    func.max(base_rows.c.usage_id).label("usage_id"),
                )
                .join(
                    base_rows,
                    and_(
                        base_rows.c.bucket_epoch == aggregate_rows.c.bucket_epoch,
                        base_rows.c.account_id == aggregate_rows.c.account_id,
                        base_rows.c.window == aggregate_rows.c.window,
                        base_rows.c.recorded_at == aggregate_rows.c.max_recorded_at,
                    ),
                )
                .group_by(
                    aggregate_rows.c.bucket_epoch,
                    aggregate_rows.c.account_id,
                    aggregate_rows.c.window,
                )
                .subquery()
            )

            stmt = (
                select(
                    aggregate_rows.c.bucket_epoch,
                    aggregate_rows.c.account_id,
                    aggregate_rows.c.window,
                    aggregate_rows.c.avg_used_percent,
                    aggregate_rows.c.samples,
                    UsageHistory.reset_at,
                    UsageHistory.window_minutes,
                    UsageHistory.recorded_at,
                )
                .join(
                    latest_ids,
                    and_(
                        latest_ids.c.bucket_epoch == aggregate_rows.c.bucket_epoch,
                        latest_ids.c.account_id == aggregate_rows.c.account_id,
                        latest_ids.c.window == aggregate_rows.c.window,
                    ),
                )
                .join(UsageHistory, UsageHistory.id == latest_ids.c.usage_id)
                .order_by(aggregate_rows.c.bucket_epoch)
            )
        else:
            base_rows = (
                select(
                    bucket_col,
                    UsageHistory.id.label("usage_id"),
                    UsageHistory.account_id.label("account_id"),
                    window_expr.label("window"),
                    UsageHistory.used_percent.label("used_percent"),
                    UsageHistory.reset_at.label("reset_at"),
                    UsageHistory.window_minutes.label("window_minutes"),
                    UsageHistory.recorded_at.label("recorded_at"),
                )
                .where(*conditions)
                .subquery()
            )

            aggregate_rows = (
                select(
                    base_rows.c.bucket_epoch,
                    base_rows.c.account_id,
                    base_rows.c.window,
                    func.avg(base_rows.c.used_percent).label("avg_used_percent"),
                    func.count(base_rows.c.usage_id).label("samples"),
                )
                .group_by(
                    base_rows.c.bucket_epoch,
                    base_rows.c.account_id,
                    base_rows.c.window,
                )
                .subquery()
            )

            latest_rows = select(
                base_rows.c.bucket_epoch,
                base_rows.c.account_id,
                base_rows.c.window,
                base_rows.c.reset_at,
                base_rows.c.window_minutes,
                base_rows.c.recorded_at,
                func.row_number()
                .over(
                    partition_by=(base_rows.c.bucket_epoch, base_rows.c.account_id, base_rows.c.window),
                    order_by=(base_rows.c.recorded_at.desc(), base_rows.c.usage_id.desc()),
                )
                .label("row_number"),
            ).subquery()

            stmt = (
                select(
                    aggregate_rows.c.bucket_epoch,
                    aggregate_rows.c.account_id,
                    aggregate_rows.c.window,
                    aggregate_rows.c.avg_used_percent,
                    aggregate_rows.c.samples,
                    latest_rows.c.reset_at,
                    latest_rows.c.window_minutes,
                    latest_rows.c.recorded_at,
                )
                .join(
                    latest_rows,
                    and_(
                        latest_rows.c.bucket_epoch == aggregate_rows.c.bucket_epoch,
                        latest_rows.c.account_id == aggregate_rows.c.account_id,
                        latest_rows.c.window == aggregate_rows.c.window,
                        latest_rows.c.row_number == 1,
                    ),
                )
                .order_by(aggregate_rows.c.bucket_epoch)
            )

        result = await self._session.execute(stmt)
        return [
            UsageTrendBucket(
                bucket_epoch=int(row.bucket_epoch),
                account_id=row.account_id,
                window=row.window,
                avg_used_percent=float(row.avg_used_percent) if row.avg_used_percent is not None else 0.0,
                samples=int(row.samples),
                reset_at=int(row.reset_at) if row.reset_at is not None else None,
                window_minutes=int(row.window_minutes) if row.window_minutes is not None else None,
                recorded_at=row.recorded_at,
            )
            for row in result.all()
        ]
