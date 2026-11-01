from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Integer, and_, cast, func, literal_column, or_, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account
from app.db.request_models import RequestLog
from app.db.session import analytics_work_mem
from app.modules.accounts.subscription_status import CANCELED_SUBSCRIPTION_STATUS
from app.modules.request_logs import daily_rollup

_INTERNAL_LIMIT_WARMUP_SOURCE = "limit_warmup"
_INTERNAL_WARMUP_REQUEST_KINDS = ("warmup", "limit_warmup")


@dataclass(frozen=True)
class DailyAggregateRow:
    date: str
    request_count: int
    error_count: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cost_usd: float
    active_accounts: int


@dataclass(frozen=True)
class ModelAggregateRow:
    model: str
    cost_usd: float


@dataclass(frozen=True)
class AccountAggregateRow:
    account_id: str | None
    alias: str | None
    cost_usd: float
    request_count: int


class ReportsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def aggregate_daily(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None = None,
        model: str | None = None,
    ) -> list[DailyAggregateRow]:
        bind = self._session.get_bind()
        dialect = bind.dialect.name if bind else "sqlite"
        if dialect == "postgresql":
            date_expr = func.date(RequestLog.requested_at)
        else:
            date_expr = func.strftime("%Y-%m-%d", RequestLog.requested_at)
        date_col = date_expr.label("date")

        conditions = [
            RequestLog.requested_at >= start_date,
            RequestLog.requested_at < end_date,
            _normal_traffic_clause(),
            _visible_account_log_clause(),
        ]
        if account_ids:
            conditions.append(RequestLog.account_id.in_(account_ids))
        if model:
            conditions.append(RequestLog.model == model)

        stmt = (
            select(
                date_col,
                func.count().label("request_count"),
                func.coalesce(
                    func.sum(cast(RequestLog.status != literal_column("'success'"), Integer)),
                    0,
                ).label("error_count"),
                func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(RequestLog.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
                func.count(func.distinct(RequestLog.account_id)).label("active_accounts"),
            )
            .outerjoin(Account, Account.id == RequestLog.account_id)
            .where(and_(*conditions))
            .group_by(date_col)
            .order_by(date_col)
        )
        await analytics_work_mem(self._session)
        result = await self._session.execute(stmt)
        rolled = await daily_rollup.report_daily_rows(
            self._session, daily_rollup.report_conditions(start_date, end_date, account_ids, model)
        )
        days: dict[str, DailyAggregateRow] = {}
        for row in [*rolled, *result.all()]:
            key = daily_rollup.merge_date(row.date)
            seen = days.get(key)
            days[key] = DailyAggregateRow(
                date=key,
                request_count=int(row.request_count or 0) + (seen.request_count if seen else 0),
                error_count=int(row.error_count or 0) + (seen.error_count if seen else 0),
                input_tokens=int(row.input_tokens or 0) + (seen.input_tokens if seen else 0),
                output_tokens=int(row.output_tokens or 0) + (seen.output_tokens if seen else 0),
                cached_input_tokens=int(row.cached_input_tokens or 0) + (seen.cached_input_tokens if seen else 0),
                cost_usd=float(row.cost_usd or 0.0) + (seen.cost_usd if seen else 0.0),
                active_accounts=max(int(row.active_accounts or 0), seen.active_accounts if seen else 0),
            )
        return [days[key] for key in sorted(days)]

    async def aggregate_by_model(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None = None,
        model: str | None = None,
    ) -> list[ModelAggregateRow]:
        conditions = [
            RequestLog.requested_at >= start_date,
            RequestLog.requested_at < end_date,
            _normal_traffic_clause(),
            RequestLog.model.is_not(None),
            _visible_account_log_clause(),
        ]
        if account_ids:
            conditions.append(RequestLog.account_id.in_(account_ids))
        if model:
            conditions.append(RequestLog.model == model)

        stmt = (
            select(
                RequestLog.model,
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
            )
            .outerjoin(Account, Account.id == RequestLog.account_id)
            .where(and_(*conditions))
            .group_by(RequestLog.model)
            .order_by(func.coalesce(func.sum(RequestLog.cost_usd), 0.0).desc())
        )
        result = await self._session.execute(stmt)
        costs = await daily_rollup.report_model_costs(
            self._session, daily_rollup.report_conditions(start_date, end_date, account_ids, model)
        )
        for row in result.all():
            costs[row.model] = costs.get(row.model, 0.0) + float(row.cost_usd)
        return [
            ModelAggregateRow(model=name, cost_usd=cost)
            for name, cost in sorted(costs.items(), key=lambda item: item[1], reverse=True)
        ]

    async def aggregate_by_account(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None = None,
        model: str | None = None,
    ) -> list[AccountAggregateRow]:
        conditions = [
            RequestLog.requested_at >= start_date,
            RequestLog.requested_at < end_date,
            _normal_traffic_clause(),
            _visible_account_log_clause(),
        ]
        if account_ids:
            conditions.append(RequestLog.account_id.in_(account_ids))
        if model:
            conditions.append(RequestLog.model == model)

        stmt = (
            select(
                RequestLog.account_id,
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
                func.count().label("request_count"),
            )
            .outerjoin(Account, Account.id == RequestLog.account_id)
            .where(and_(*conditions))
            .group_by(RequestLog.account_id)
            .order_by(func.coalesce(func.sum(RequestLog.cost_usd), 0.0).desc())
        )
        result = await self._session.execute(stmt)
        merged = await daily_rollup.report_account_rows(
            self._session, daily_rollup.report_conditions(start_date, end_date, account_ids, model)
        )
        for row in result.all():
            cost, count = merged.get(row.account_id, (0.0, 0))
            merged[row.account_id] = (cost + float(row.cost_usd), count + int(row.request_count))
        rows = [
            AccountAggregateRow(account_id=account_id, alias=None, cost_usd=cost, request_count=count)
            for account_id, (cost, count) in sorted(merged.items(), key=lambda item: item[1][0], reverse=True)
        ]

        account_ids_found = [row.account_id for row in rows if row.account_id]
        alias_map: dict[str | None, str | None] = {}
        if account_ids_found:
            alias_result = await self._session.execute(
                select(Account.id, Account.alias).where(Account.id.in_(account_ids_found))
            )
            alias_map = {account_id: alias for account_id, alias in alias_result.all()}

        return [
            AccountAggregateRow(
                account_id=row.account_id,
                alias=alias_map.get(row.account_id),
                cost_usd=float(row.cost_usd),
                request_count=int(row.request_count),
            )
            for row in rows
        ]

    async def count_active_accounts(
        self,
        start_date: datetime,
        end_date: datetime,
        account_ids: list[str] | None = None,
        model: str | None = None,
    ) -> int:
        conditions = [
            RequestLog.requested_at >= start_date,
            RequestLog.requested_at < end_date,
            _normal_traffic_clause(),
            RequestLog.account_id.is_not(None),
            _visible_account_log_clause(),
        ]
        if account_ids:
            conditions.append(RequestLog.account_id.in_(account_ids))
        if model:
            conditions.append(RequestLog.model == model)

        live = (
            select(RequestLog.account_id)
            .outerjoin(Account, Account.id == RequestLog.account_id)
            .where(and_(*conditions))
        )
        rolled = daily_rollup.report_account_ids(
            daily_rollup.report_conditions(start_date, end_date, account_ids, model)
        )
        accounts = union(live, rolled).subquery()
        result = await self._session.execute(select(func.count()).select_from(accounts))
        return int(result.scalar_one() or 0)


def _normal_traffic_clause():
    return and_(
        or_(RequestLog.source.is_(None), RequestLog.source != _INTERNAL_LIMIT_WARMUP_SOURCE),
        or_(
            RequestLog.request_kind.is_(None),
            RequestLog.request_kind.not_in(_INTERNAL_WARMUP_REQUEST_KINDS),
        ),
    )


def _visible_account_log_clause():
    return or_(
        RequestLog.account_id.is_(None),
        Account.id.is_(None),
        Account.subscription_status.is_(None),
        func.lower(func.trim(Account.subscription_status)) != CANCELED_SUBSCRIPTION_STATUS,
    )
