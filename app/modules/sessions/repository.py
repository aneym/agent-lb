from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Integer, and_, case, cast, func, literal, literal_column, select
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


@dataclass(frozen=True, slots=True)
class SessionSeriesRow:
    bucket_start: datetime
    model: str
    reasoning_effort: str | None
    requests: int
    output_tokens: int
    cached_input_tokens: int
    cost_usd: float


@dataclass(frozen=True, slots=True)
class SessionSeatRow:
    model: str
    reasoning_effort: str | None
    requests: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cost_usd: float
    errors: int


@dataclass(frozen=True, slots=True)
class SessionHistogramRow:
    label: str
    count: int


@dataclass(frozen=True, slots=True)
class SessionSparklineRow:
    session_id: str
    bucket_index: int
    requests: int


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

    async def get_aggregate(
        self,
        session_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> SessionAggregateRow | None:
        return await self._single_aggregate_statement(
            session_id,
            since=since,
            until=until,
        )

    async def list_models(
        self,
        session_ids: list[str],
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[SessionModelRow]:
        if not session_ids:
            return []
        conditions = [self._eligible_clause(), RequestLog.session_id.in_(session_ids)]
        if since is not None:
            conditions.append(RequestLog.requested_at >= since)
        if until is not None:
            conditions.append(RequestLog.requested_at < until)
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

    async def list_sparklines(
        self,
        session_ids: list[str],
        *,
        since: datetime,
        until: datetime,
    ) -> list[SessionSparklineRow]:
        if not session_ids:
            return []
        epoch = self._epoch_expression(RequestLog.requested_at)
        since_epoch = self._epoch_expression(literal(since))
        window_seconds = max(1.0, (until - since).total_seconds())
        bucket_index = cast(func.floor((epoch - since_epoch) * 24 / window_seconds), Integer)
        statement = (
            select(
                RequestLog.session_id,
                bucket_index.label("bucket_index"),
                func.count().label("requests"),
            )
            .where(
                self._eligible_clause(),
                RequestLog.session_id.in_(session_ids),
                RequestLog.requested_at >= since,
                RequestLog.requested_at < until,
            )
            .group_by(RequestLog.session_id, bucket_index)
            .order_by(RequestLog.session_id.asc(), bucket_index.asc())
        )
        rows = (await self._session.execute(statement)).all()
        return [
            SessionSparklineRow(
                session_id=str(row.session_id),
                bucket_index=int(row.bucket_index),
                requests=int(row.requests),
            )
            for row in rows
        ]

    async def list_series(
        self,
        session_id: str,
        *,
        since: datetime,
        until: datetime,
        bucket_seconds: int,
    ) -> list[SessionSeriesRow]:
        epoch = self._epoch_expression(RequestLog.requested_at)
        bucket_epoch = func.floor(epoch / bucket_seconds) * bucket_seconds
        bucket_start = self._datetime_from_epoch(bucket_epoch)
        statement = (
            select(
                bucket_start.label("bucket_start"),
                RequestLog.model,
                RequestLog.reasoning_effort,
                func.count().label("requests"),
                func.coalesce(func.sum(RequestLog.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
            )
            .where(
                self._eligible_clause(),
                RequestLog.session_id == session_id,
                RequestLog.requested_at >= since,
                RequestLog.requested_at < until,
            )
            .group_by(bucket_epoch, RequestLog.model, RequestLog.reasoning_effort)
            .order_by(bucket_epoch.asc(), RequestLog.model.asc(), RequestLog.reasoning_effort.asc())
        )
        rows = (await self._session.execute(statement)).all()
        return [
            SessionSeriesRow(
                bucket_start=row.bucket_start,
                model=str(row.model),
                reasoning_effort=(
                    str(row.reasoning_effort) if row.reasoning_effort is not None else None
                ),
                requests=int(row.requests),
                output_tokens=int(row.output_tokens),
                cached_input_tokens=int(row.cached_input_tokens),
                cost_usd=float(row.cost_usd),
            )
            for row in rows
        ]

    async def list_seats(
        self,
        session_id: str,
        *,
        since: datetime,
        until: datetime,
    ) -> list[SessionSeatRow]:
        statement = (
            select(
                RequestLog.model,
                RequestLog.reasoning_effort,
                func.count().label("requests"),
                func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(RequestLog.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
                func.coalesce(
                    func.sum(cast(RequestLog.status != literal_column("'success'"), Integer)),
                    0,
                ).label("errors"),
            )
            .where(
                self._eligible_clause(),
                RequestLog.session_id == session_id,
                RequestLog.requested_at >= since,
                RequestLog.requested_at < until,
            )
            .group_by(RequestLog.model, RequestLog.reasoning_effort)
            .order_by(func.count().desc(), RequestLog.model.asc(), RequestLog.reasoning_effort.asc())
        )
        rows = (await self._session.execute(statement)).all()
        return [
            SessionSeatRow(
                model=str(row.model),
                reasoning_effort=(
                    str(row.reasoning_effort) if row.reasoning_effort is not None else None
                ),
                requests=int(row.requests),
                input_tokens=int(row.input_tokens),
                output_tokens=int(row.output_tokens),
                cached_input_tokens=int(row.cached_input_tokens),
                cost_usd=float(row.cost_usd),
                errors=int(row.errors),
            )
            for row in rows
        ]

    async def list_latency_histogram(
        self,
        session_id: str,
        *,
        since: datetime,
        until: datetime,
    ) -> list[SessionHistogramRow]:
        label = case(
            (RequestLog.latency_ms < 1_000, "0-1s"),
            (RequestLog.latency_ms <= 2_000, "1-2s"),
            (RequestLog.latency_ms <= 5_000, "2-5s"),
            (RequestLog.latency_ms <= 10_000, "5-10s"),
            (RequestLog.latency_ms <= 30_000, "10-30s"),
            (RequestLog.latency_ms <= 60_000, "30-60s"),
            else_=">60s",
        )
        return await self._histogram(
            session_id,
            since=since,
            until=until,
            label=label,
            value_is_present=RequestLog.latency_ms.is_not(None),
        )

    async def list_tokens_per_request_histogram(
        self,
        session_id: str,
        *,
        since: datetime,
        until: datetime,
    ) -> list[SessionHistogramRow]:
        tokens = func.coalesce(RequestLog.input_tokens, 0) + func.coalesce(RequestLog.output_tokens, 0)
        label = case(
            (tokens < 100, "<100"),
            (tokens <= 500, "100-500"),
            (tokens <= 2_000, "500-2K"),
            (tokens <= 10_000, "2K-10K"),
            (tokens <= 50_000, "10K-50K"),
            else_=">50K",
        )
        return await self._histogram(
            session_id,
            since=since,
            until=until,
            label=label,
            value_is_present=and_(
                RequestLog.input_tokens.is_not(None),
                RequestLog.output_tokens.is_not(None),
            ),
        )

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

    async def _histogram(
        self,
        session_id: str,
        *,
        since: datetime,
        until: datetime,
        label: ColumnElement[str],
        value_is_present: ColumnElement[bool],
    ) -> list[SessionHistogramRow]:
        statement = (
            select(label.label("label"), func.count().label("count"))
            .where(
                self._eligible_clause(),
                RequestLog.session_id == session_id,
                RequestLog.requested_at >= since,
                RequestLog.requested_at < until,
                value_is_present,
            )
            .group_by(label)
        )
        rows = (await self._session.execute(statement)).all()
        return [SessionHistogramRow(label=str(row.label), count=int(row.count)) for row in rows]

    def _epoch_expression(self, value) -> ColumnElement[float]:
        bind = self._session.get_bind()
        dialect = bind.dialect.name if bind else "sqlite"
        if dialect == "postgresql":
            return cast(func.extract("epoch", value), Integer)
        return cast(func.strftime("%s", value), Integer)

    def _datetime_from_epoch(self, value) -> ColumnElement[datetime]:
        bind = self._session.get_bind()
        dialect = bind.dialect.name if bind else "sqlite"
        if dialect == "postgresql":
            return func.to_timestamp(value)
        return func.datetime(value, "unixepoch")

    async def _aggregates_for_session_ids(
        self,
        session_ids: list[str],
    ) -> tuple[list[SessionAggregateRow], int]:
        if not session_ids:
            return [], 0
        row = await self._single_aggregate_statement(session_ids[0])
        return ([row] if row is not None else []), int(row is not None)

    async def _single_aggregate_statement(
        self,
        session_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> SessionAggregateRow | None:
        condition_parts = [self._eligible_clause(), RequestLog.session_id == session_id]
        if since is not None:
            condition_parts.append(RequestLog.requested_at >= since)
        if until is not None:
            condition_parts.append(RequestLog.requested_at < until)
        conditions = and_(*condition_parts)
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
