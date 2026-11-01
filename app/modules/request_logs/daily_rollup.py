from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import ColumnElement, and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account
from app.db.rollup_models import RequestLogDaily
from app.modules.accounts.subscription_status import CANCELED_SUBSCRIPTION_STATUS

WARMUP_REQUEST_KINDS = ("warmup", "limit_warmup")
LIMIT_WARMUP_SOURCE = "limit_warmup"


@dataclass(frozen=True)
class RollupTotals:
    requests: int
    input_tokens: int
    output_or_reasoning_tokens: int
    cached_input_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    cost_usd: float


def not_warmup() -> ColumnElement[bool]:
    return RequestLogDaily.request_kind.not_in(WARMUP_REQUEST_KINDS)


def not_forwarded_edge() -> ColumnElement[bool]:
    return or_(RequestLogDaily.source.is_(None), RequestLogDaily.source.not_like("edge:%"))


def normal_traffic() -> ColumnElement[bool]:
    return and_(or_(RequestLogDaily.source.is_(None), RequestLogDaily.source != LIMIT_WARMUP_SOURCE), not_warmup())


def visible_account() -> ColumnElement[bool]:
    return or_(
        RequestLogDaily.account_id.is_(None),
        Account.id.is_(None),
        Account.subscription_status.is_(None),
        func.lower(func.trim(Account.subscription_status)) != CANCELED_SUBSCRIPTION_STATUS,
    )


def day_range(start: datetime, end: datetime | None = None) -> list[ColumnElement[bool]]:
    clauses = [RequestLogDaily.day >= start.date()]
    if end is not None:
        clauses.append(RequestLogDaily.day < end.date())
    return clauses


def _totals_columns():
    return (
        func.coalesce(func.sum(RequestLogDaily.requests), 0),
        func.coalesce(func.sum(RequestLogDaily.input_tokens), 0),
        func.coalesce(func.sum(RequestLogDaily.output_or_reasoning_tokens), 0),
        func.coalesce(func.sum(RequestLogDaily.cached_input_tokens), 0),
        func.coalesce(func.sum(RequestLogDaily.cache_creation_tokens), 0),
        func.coalesce(func.sum(RequestLogDaily.cache_read_tokens), 0),
        func.coalesce(func.sum(RequestLogDaily.cost_usd), 0.0),
    )


def _totals(row) -> RollupTotals:
    return RollupTotals(
        requests=int(row[0] or 0),
        input_tokens=int(row[1] or 0),
        output_or_reasoning_tokens=int(row[2] or 0),
        cached_input_tokens=int(row[3] or 0),
        cache_creation_tokens=int(row[4] or 0),
        cache_read_tokens=int(row[5] or 0),
        cost_usd=float(row[6] or 0.0),
    )


async def account_totals(session: AsyncSession, account_ids: list[str] | None) -> dict[str, RollupTotals]:
    conditions = [
        RequestLogDaily.account_id.is_not(None),
        not_warmup(),
        RequestLogDaily.deleted.is_(False),
        RequestLogDaily.superseded.is_(False),
    ]
    if account_ids:
        conditions.append(RequestLogDaily.account_id.in_(account_ids))
    stmt = (
        select(RequestLogDaily.account_id, *_totals_columns()).where(*conditions).group_by(RequestLogDaily.account_id)
    )
    return {row[0]: _totals(row[1:]) for row in (await session.execute(stmt)).all()}


async def api_key_totals(session: AsyncSession, key_id: str | None = None) -> dict[str, RollupTotals]:
    conditions = [RequestLogDaily.api_key_id.is_not(None), not_warmup(), not_forwarded_edge()]
    if key_id is not None:
        conditions.append(RequestLogDaily.api_key_id == key_id)
    stmt = (
        select(RequestLogDaily.api_key_id, *_totals_columns()).where(*conditions).group_by(RequestLogDaily.api_key_id)
    )
    return {row[0]: _totals(row[1:]) for row in (await session.execute(stmt)).all()}


async def public_usage_rows(session: AsyncSession, since: datetime) -> list:
    stmt = (
        select(
            RequestLogDaily.day.label("d"),
            RequestLogDaily.model.label("model"),
            RequestLogDaily.provider.label("provider"),
            func.sum(RequestLogDaily.requests).label("requests"),
            func.sum(RequestLogDaily.input_tokens).label("input_tokens"),
            func.sum(RequestLogDaily.output_tokens).label("output_tokens"),
            func.sum(RequestLogDaily.cached_input_tokens).label("cached_input_tokens"),
            func.sum(RequestLogDaily.reasoning_tokens).label("reasoning_tokens"),
            func.sum(RequestLogDaily.cost_usd).label("cost_usd"),
            func.sum(RequestLogDaily.latency_ms_sum).label("latency_sum"),
            func.sum(RequestLogDaily.latency_count).label("latency_count"),
            func.coalesce(func.sum(RequestLogDaily.requests).filter(RequestLogDaily.status == "success"), 0).label(
                "success_count"
            ),
        )
        .where(*day_range(since), normal_traffic())
        .group_by(RequestLogDaily.day, RequestLogDaily.model, RequestLogDaily.provider)
    )
    return list((await session.execute(stmt)).all())


def report_conditions(
    start: datetime, end: datetime, account_ids: list[str] | None, model: str | None
) -> list[ColumnElement[bool]]:
    conditions = [*day_range(start, end), normal_traffic(), visible_account()]
    if account_ids:
        conditions.append(RequestLogDaily.account_id.in_(account_ids))
    if model:
        conditions.append(RequestLogDaily.model == model)
    return conditions


def report_select(*columns, conditions: list[ColumnElement[bool]]):
    return select(*columns).outerjoin(Account, Account.id == RequestLogDaily.account_id).where(and_(*conditions))


async def report_daily_rows(session: AsyncSession, conditions: list[ColumnElement[bool]]) -> list:
    stmt = report_select(
        RequestLogDaily.day.label("date"),
        func.sum(RequestLogDaily.requests).label("request_count"),
        func.coalesce(func.sum(RequestLogDaily.requests).filter(RequestLogDaily.status != "success"), 0).label(
            "error_count"
        ),
        func.sum(RequestLogDaily.input_tokens).label("input_tokens"),
        func.sum(RequestLogDaily.output_tokens).label("output_tokens"),
        func.sum(RequestLogDaily.cached_input_tokens).label("cached_input_tokens"),
        func.sum(RequestLogDaily.cost_usd).label("cost_usd"),
        func.count(func.distinct(RequestLogDaily.account_id)).label("active_accounts"),
        conditions=conditions,
    ).group_by(RequestLogDaily.day)
    return list((await session.execute(stmt)).all())


async def report_model_costs(session: AsyncSession, conditions: list[ColumnElement[bool]]) -> dict[str, float]:
    stmt = report_select(RequestLogDaily.model, func.sum(RequestLogDaily.cost_usd), conditions=conditions).group_by(
        RequestLogDaily.model
    )
    return {model: float(cost or 0.0) for model, cost in (await session.execute(stmt)).all()}


async def report_account_rows(
    session: AsyncSession, conditions: list[ColumnElement[bool]]
) -> dict[str | None, tuple[float, int]]:
    stmt = report_select(
        RequestLogDaily.account_id,
        func.sum(RequestLogDaily.cost_usd),
        func.sum(RequestLogDaily.requests),
        conditions=conditions,
    ).group_by(RequestLogDaily.account_id)
    return {
        account_id: (float(cost or 0.0), int(count or 0))
        for account_id, cost, count in (await session.execute(stmt)).all()
    }


def report_account_ids(conditions: list[ColumnElement[bool]]):
    return report_select(RequestLogDaily.account_id, conditions=[*conditions, RequestLogDaily.account_id.is_not(None)])


async def forget_account(session: AsyncSession, account_id: str, *, delete_history: bool) -> None:
    if delete_history:
        await session.execute(delete(RequestLogDaily).where(RequestLogDaily.account_id == account_id))
        return
    await session.execute(
        update(RequestLogDaily).where(RequestLogDaily.account_id == account_id).values(account_id=None, deleted=True)
    )


def merge_date(value: date | datetime | str) -> str:
    return value.strftime("%Y-%m-%d") if isinstance(value, (date, datetime)) else str(value)[:10]
