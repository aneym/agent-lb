from __future__ import annotations

from collections.abc import Collection
from datetime import datetime

from sqlalchemy import Integer, and_, case, cast, false, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.usage.types import (
    BucketModelAggregate,
    RequestActivityAggregate,
    UsageCostByModel,
    UsageCostSummary,
    UsageMetricsSummary,
)
from app.db.models import Account, RequestKind, RequestLog
from app.modules.accounts.subscription_status import CANCELED_SUBSCRIPTION_STATUS


class RequestLogAggregates:
    _session: AsyncSession

    @staticmethod
    def _exclude_warmup_clause() -> ColumnElement[bool]:
        return RequestLog.request_kind.not_in((RequestKind.WARMUP.value, "limit_warmup"))

    async def anthropic_cache_tokens_since(self, since: datetime, until: datetime) -> tuple[int, int, int, int, int]:

        complete = and_(
            RequestLog.input_tokens.is_not(None),
            RequestLog.cache_creation_tokens.is_not(None),
            RequestLog.cache_read_tokens.is_not(None),
        )
        result = await self._session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(case((complete, 0), else_=1)), 0),
                func.coalesce(func.sum(RequestLog.input_tokens), 0),
                func.coalesce(func.sum(RequestLog.cache_creation_tokens), 0),
                func.coalesce(func.sum(RequestLog.cache_read_tokens), 0),
            ).where(
                RequestLog.provider == "anthropic",
                RequestLog.status == "success",
                RequestLog.requested_at >= since,
                RequestLog.requested_at <= until,
                self._exclude_warmup_clause(),
            )
        )
        count, incomplete, uncached, creation, read = result.one()
        return int(count), int(incomplete), int(uncached), int(creation), int(read)

    async def provider_tokens_since(self, provider: str, since: datetime) -> int:
        result = await self._session.execute(
            select(
                func.coalesce(
                    func.sum(func.coalesce(RequestLog.input_tokens, 0) + func.coalesce(RequestLog.output_tokens, 0)), 0
                )
            ).where(RequestLog.provider == provider, RequestLog.requested_at >= since)
        )
        return int(result.scalar_one())

    async def aggregate_by_bucket(
        self,
        since: datetime,
        bucket_seconds: int = 21600,
        *,
        exclude_canceled_subscription_accounts: bool = False,
    ) -> list[BucketModelAggregate]:
        bind = self._session.get_bind()
        dialect = bind.dialect.name if bind else "sqlite"
        if dialect == "postgresql":
            bucket_expr = func.floor(func.extract("epoch", RequestLog.requested_at) / bucket_seconds) * bucket_seconds
        else:
            epoch_col = cast(func.strftime("%s", RequestLog.requested_at), Integer)
            bucket_expr = cast(epoch_col / bucket_seconds, Integer) * bucket_seconds
        bucket_col = bucket_expr.label("bucket_epoch")

        conditions = [
            RequestLog.requested_at >= since,
            self._exclude_warmup_clause(),
        ]
        if exclude_canceled_subscription_accounts:
            conditions.append(_visible_account_log_clause())

        stmt = select(
            bucket_col,
            RequestLog.model,
            RequestLog.service_tier,
            func.count().label("request_count"),
            func.sum(cast(RequestLog.status != literal_column("'success'"), Integer)).label("error_count"),
            func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(RequestLog.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
            func.coalesce(func.sum(RequestLog.reasoning_tokens), 0).label("reasoning_tokens"),
            func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
        )
        if exclude_canceled_subscription_accounts:
            stmt = stmt.outerjoin(Account, Account.id == RequestLog.account_id)
        stmt = (
            stmt.where(and_(*conditions))
            .group_by(bucket_col, RequestLog.model, RequestLog.service_tier)
            .order_by(bucket_col)
        )
        result = await self._session.execute(stmt)
        return [
            BucketModelAggregate(
                bucket_epoch=int(row.bucket_epoch),
                model=row.model,
                service_tier=row.service_tier,
                request_count=int(row.request_count),
                error_count=int(row.error_count),
                input_tokens=int(row.input_tokens),
                output_tokens=int(row.output_tokens),
                cached_input_tokens=int(row.cached_input_tokens),
                reasoning_tokens=int(row.reasoning_tokens),
                cost_usd=float(row.cost_usd or 0.0),
            )
            for row in result.all()
        ]

    async def aggregate_activity_since(
        self,
        since: datetime,
        *,
        exclude_canceled_subscription_accounts: bool = False,
    ) -> RequestActivityAggregate:
        conditions = [
            RequestLog.requested_at >= since,
            self._exclude_warmup_clause(),
        ]
        if exclude_canceled_subscription_accounts:
            conditions.append(_visible_account_log_clause())

        stmt = select(
            func.count().label("request_count"),
            func.coalesce(
                func.sum(cast(RequestLog.status != literal_column("'success'"), Integer)),
                0,
            ).label("error_count"),
            func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(RequestLog.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
            func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
        )
        if exclude_canceled_subscription_accounts:
            stmt = stmt.outerjoin(Account, Account.id == RequestLog.account_id)
        stmt = stmt.where(and_(*conditions))
        result = await self._session.execute(stmt)
        row = result.one()
        return RequestActivityAggregate(
            request_count=int(row.request_count),
            error_count=int(row.error_count),
            input_tokens=int(row.input_tokens),
            output_tokens=int(row.output_tokens),
            cached_input_tokens=int(row.cached_input_tokens),
            cost_usd=float(row.cost_usd or 0.0),
        )

    async def top_error_since(
        self,
        since: datetime,
        *,
        exclude_canceled_subscription_accounts: bool = False,
    ) -> str | None:
        conditions = [
            RequestLog.requested_at >= since,
            self._exclude_warmup_clause(),
            RequestLog.status != "success",
            RequestLog.error_code.is_not(None),
        ]
        if exclude_canceled_subscription_accounts:
            conditions.append(_visible_account_log_clause())

        stmt = (
            select(RequestLog.error_code, func.count(RequestLog.id).label("error_count"))
            .group_by(RequestLog.error_code)
            .order_by(func.count(RequestLog.id).desc(), RequestLog.error_code.asc())
            .limit(1)
        )
        if exclude_canceled_subscription_accounts:
            stmt = stmt.outerjoin(Account, Account.id == RequestLog.account_id)
        stmt = stmt.where(and_(*conditions))
        result = await self._session.execute(stmt)
        row = result.first()
        return str(row[0]) if row and row[0] else None

    async def aggregate_usage_window(
        self,
        since: datetime,
        *,
        account_ids: Collection[str],
        include_unattributed: bool,
    ) -> tuple[UsageMetricsSummary, UsageCostSummary]:

        bind = self._session.get_bind()
        dialect = bind.dialect.name if bind else "sqlite"

        conditions = [
            RequestLog.requested_at >= since,
            self._exclude_warmup_clause(),
            _account_scope_clause(account_ids, include_unattributed=include_unattributed),
        ]

        output_tokens_expr = func.coalesce(RequestLog.output_tokens, RequestLog.reasoning_tokens, 0)
        tokens_expr = func.coalesce(RequestLog.input_tokens, 0) + output_tokens_expr
        cached_clamped = _least(dialect, _greatest(dialect, RequestLog.cached_input_tokens, 0), RequestLog.input_tokens)
        cached_expr = case(
            (RequestLog.cached_input_tokens.is_(None), 0),
            (RequestLog.input_tokens.is_not(None), cached_clamped),
            else_=_greatest(dialect, RequestLog.cached_input_tokens, 0),
        )

        overall_stmt = select(
            func.count().label("request_count"),
            func.coalesce(func.sum(cast(RequestLog.status != literal_column("'success'"), Integer)), 0).label(
                "error_count"
            ),
            func.coalesce(func.sum(tokens_expr), 0).label("tokens_sum"),
            func.coalesce(func.sum(cached_expr), 0).label("cached_tokens_sum"),
        ).where(and_(*conditions))
        overall_row = (await self._session.execute(overall_stmt)).one()

        model_stmt = (
            select(RequestLog.model, func.sum(RequestLog.cost_usd).label("cost_usd"))
            .where(and_(*conditions, RequestLog.cost_usd.is_not(None)))
            .group_by(RequestLog.model)
        )
        model_rows = (await self._session.execute(model_stmt)).all()

        top_error_stmt = (
            select(RequestLog.error_code, func.count().label("error_count"))
            .where(and_(*conditions, RequestLog.status != "success", RequestLog.error_code.is_not(None)))
            .group_by(RequestLog.error_code)
            .order_by(func.count().desc(), RequestLog.error_code.asc())
            .limit(1)
        )
        top_error_row = (await self._session.execute(top_error_stmt)).first()
        top_error = str(top_error_row[0]) if top_error_row and top_error_row[0] else None

        request_count = int(overall_row.request_count)
        error_rate = (int(overall_row.error_count) / request_count) if request_count > 0 else None
        metrics = UsageMetricsSummary(
            requests_7d=request_count,
            tokens_secondary_window=int(overall_row.tokens_sum),
            cached_tokens_secondary_window=int(overall_row.cached_tokens_sum),
            error_rate_7d=error_rate,
            top_error=top_error,
        )

        total_cost = 0.0
        by_model: list[UsageCostByModel] = []
        for row in sorted(model_rows, key=lambda r: r.model):
            row_cost = float(row.cost_usd or 0.0)
            total_cost += row_cost
            by_model.append(UsageCostByModel(model=row.model, usd=round(row_cost, 6)))
        cost = UsageCostSummary(currency="USD", total_usd_7d=round(total_cost, 6), by_model=by_model)

        return metrics, cost

    async def aggregate_callers_window(
        self,
        since: datetime,
        until: datetime,
        *,
        account_ids: Collection[str],
    ) -> list[tuple[str, str, int, int, int, float]]:

        caller_user = func.coalesce(RequestLog.caller_user, "unattributed")
        caller_machine = func.coalesce(RequestLog.caller_machine, "unattributed")
        stmt = (
            select(
                caller_user,
                caller_machine,
                func.count().label("requests"),
                func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
                func.coalesce(
                    func.sum(func.coalesce(RequestLog.output_tokens, RequestLog.reasoning_tokens, 0)), 0
                ).label("output_tokens"),
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
            )
            .where(
                RequestLog.requested_at >= since,
                RequestLog.requested_at <= until,
                self._exclude_warmup_clause(),
                _account_scope_clause(account_ids, include_unattributed=True),
            )
            .group_by(caller_user, caller_machine)
            .order_by(caller_user, caller_machine)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            (
                user or "unattributed",
                machine or "unattributed",
                int(requests),
                int(input_tokens),
                int(output_tokens),
                round(float(cost_usd), 6),
            )
            for user, machine, requests, input_tokens, output_tokens, cost_usd in rows
        ]


def _visible_account_log_clause() -> ColumnElement[bool]:
    return or_(
        RequestLog.account_id.is_(None),
        Account.id.is_(None),
        Account.subscription_status.is_(None),
        func.lower(func.trim(Account.subscription_status)) != CANCELED_SUBSCRIPTION_STATUS,
    )


def _account_scope_clause(account_ids: Collection[str], *, include_unattributed: bool) -> ColumnElement[bool]:
    ids = list(account_ids)
    membership = RequestLog.account_id.in_(ids) if ids else false()
    if include_unattributed:
        return or_(RequestLog.account_id.is_(None), membership)
    return membership


def _least(dialect: str, a: ColumnElement, b: ColumnElement) -> ColumnElement:

    if dialect == "postgresql":
        return func.least(a, b)
    return func.min(a, b)


def _greatest(dialect: str, a: ColumnElement, b: ColumnElement) -> ColumnElement:
    if dialect == "postgresql":
        return func.greatest(a, b)
    return func.max(a, b)
