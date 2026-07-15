from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Integer, and_, cast, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import RequestLog


@dataclass(frozen=True, slots=True)
class SessionAggregateRow:
    session_id: str
    provider: str
    useragent_group: str | None
    requests: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cost_usd: float
    errors: int
    first_seen: datetime
    last_seen: datetime


@dataclass(frozen=True, slots=True)
class SessionModelRow:
    session_id: str
    model: str
    requests: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cost_usd: float


class SessionsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _eligible_clause() -> ColumnElement[bool]:
        return and_(
            RequestLog.session_id.is_not(None),
            RequestLog.session_id.not_like(r"turn\_%", escape="\\"),
            RequestLog.session_id.not_like(r"http\_turn\_%", escape="\\"),
            RequestLog.deleted_at.is_(None),
        )

    async def list_aggregates(
        self,
        *,
        since: datetime,
        limit: int,
        offset: int,
    ) -> tuple[list[SessionAggregateRow], int]:
        conditions = and_(self._eligible_clause(), RequestLog.requested_at >= since)
        provider_rank = (
            select(
                RequestLog.session_id.label("session_id"),
                RequestLog.provider.label("provider"),
                func.row_number()
                .over(
                    partition_by=RequestLog.session_id,
                    order_by=(func.count().desc(), RequestLog.provider.asc()),
                )
                .label("rank"),
            )
            .where(conditions)
            .group_by(RequestLog.session_id, RequestLog.provider)
            .subquery()
        )
        useragent_rank = (
            select(
                RequestLog.session_id.label("session_id"),
                RequestLog.useragent_group.label("useragent_group"),
                func.row_number()
                .over(
                    partition_by=RequestLog.session_id,
                    order_by=(func.count().desc(), RequestLog.useragent_group.asc()),
                )
                .label("rank"),
            )
            .where(conditions, RequestLog.useragent_group.is_not(None))
            .group_by(RequestLog.session_id, RequestLog.useragent_group)
            .subquery()
        )
        aggregate = (
            select(
                RequestLog.session_id.label("session_id"),
                func.count().label("requests"),
                func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(RequestLog.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
                func.coalesce(
                    func.sum(cast(RequestLog.status != literal_column("'success'"), Integer)),
                    0,
                ).label("errors"),
                func.min(RequestLog.requested_at).label("first_seen"),
                func.max(RequestLog.requested_at).label("last_seen"),
            )
            .where(conditions)
            .group_by(RequestLog.session_id)
            .subquery()
        )
        total = int(
            (await self._session.execute(select(func.count()).select_from(aggregate))).scalar_one()
        )
        statement = (
            select(
                aggregate,
                provider_rank.c.provider,
                useragent_rank.c.useragent_group,
            )
            .join(
                provider_rank,
                and_(provider_rank.c.session_id == aggregate.c.session_id, provider_rank.c.rank == 1),
            )
            .outerjoin(
                useragent_rank,
                and_(useragent_rank.c.session_id == aggregate.c.session_id, useragent_rank.c.rank == 1),
            )
            .order_by(aggregate.c.last_seen.desc(), aggregate.c.session_id.asc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(statement)).all()
        return [self._aggregate_row(row) for row in rows], total

    async def get_aggregate(self, session_id: str) -> SessionAggregateRow | None:
        rows, _ = await self._aggregates_for_session_ids([session_id])
        return rows[0] if rows else None

    async def list_models(
        self,
        session_ids: list[str],
        *,
        since: datetime | None = None,
    ) -> list[SessionModelRow]:
        if not session_ids:
            return []
        conditions = [self._eligible_clause(), RequestLog.session_id.in_(session_ids)]
        if since is not None:
            conditions.append(RequestLog.requested_at >= since)
        statement = (
            select(
                RequestLog.session_id,
                RequestLog.model,
                func.count().label("requests"),
                func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(RequestLog.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
            )
            .where(and_(*conditions))
            .group_by(RequestLog.session_id, RequestLog.model)
            .order_by(RequestLog.session_id.asc(), func.count().desc(), RequestLog.model.asc())
        )
        rows = (await self._session.execute(statement)).all()
        return [
            SessionModelRow(
                session_id=str(row.session_id),
                model=str(row.model),
                requests=int(row.requests),
                input_tokens=int(row.input_tokens),
                output_tokens=int(row.output_tokens),
                cached_input_tokens=int(row.cached_input_tokens),
                cost_usd=float(row.cost_usd),
            )
            for row in rows
        ]

    async def list_recent_requests(self, session_id: str, *, limit: int = 50) -> list[RequestLog]:
        statement = (
            select(RequestLog)
            .where(self._eligible_clause(), RequestLog.session_id == session_id)
            .order_by(RequestLog.requested_at.desc(), RequestLog.id.desc())
            .limit(limit)
        )
        return list((await self._session.execute(statement)).scalars().all())

    async def resolve_session_id(self, value: str) -> list[str]:
        exact_statement = (
            select(RequestLog.session_id)
            .where(self._eligible_clause(), RequestLog.session_id == value)
            .limit(1)
        )
        exact = (await self._session.execute(exact_statement)).scalar_one_or_none()
        if exact is not None:
            return [str(exact)]

        prefix_statement = (
            select(RequestLog.session_id)
            .where(self._eligible_clause(), RequestLog.session_id.startswith(value, autoescape=True))
            .distinct()
            .order_by(RequestLog.session_id.asc())
            .limit(2)
        )
        return [
            str(session_id)
            for session_id in (await self._session.execute(prefix_statement)).scalars().all()
        ]

    async def _aggregates_for_session_ids(
        self,
        session_ids: list[str],
    ) -> tuple[list[SessionAggregateRow], int]:
        if not session_ids:
            return [], 0
        row = await self._single_aggregate_statement(session_ids[0])
        return ([row] if row is not None else []), int(row is not None)

    async def _single_aggregate_statement(self, session_id: str) -> SessionAggregateRow | None:
        conditions = and_(self._eligible_clause(), RequestLog.session_id == session_id)
        provider = (
            select(RequestLog.provider)
            .where(conditions)
            .group_by(RequestLog.provider)
            .order_by(func.count().desc(), RequestLog.provider.asc())
            .limit(1)
            .scalar_subquery()
        )
        useragent_group = (
            select(RequestLog.useragent_group)
            .where(conditions, RequestLog.useragent_group.is_not(None))
            .group_by(RequestLog.useragent_group)
            .order_by(func.count().desc(), RequestLog.useragent_group.asc())
            .limit(1)
            .scalar_subquery()
        )
        statement = select(
            RequestLog.session_id.label("session_id"),
            provider.label("provider"),
            useragent_group.label("useragent_group"),
            func.count().label("requests"),
            func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(RequestLog.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
            func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
            func.coalesce(
                func.sum(cast(RequestLog.status != literal_column("'success'"), Integer)),
                0,
            ).label("errors"),
            func.min(RequestLog.requested_at).label("first_seen"),
            func.max(RequestLog.requested_at).label("last_seen"),
        ).where(conditions).group_by(RequestLog.session_id)
        row = (await self._session.execute(statement)).first()
        return self._aggregate_row(row) if row is not None else None

    @staticmethod
    def _aggregate_row(row) -> SessionAggregateRow:
        return SessionAggregateRow(
            session_id=str(row.session_id),
            provider=str(row.provider),
            useragent_group=str(row.useragent_group) if row.useragent_group is not None else None,
            requests=int(row.requests),
            input_tokens=int(row.input_tokens),
            output_tokens=int(row.output_tokens),
            cached_input_tokens=int(row.cached_input_tokens),
            cost_usd=float(row.cost_usd),
            errors=int(row.errors),
            first_seen=row.first_seen,
            last_seen=row.last_seen,
        )
