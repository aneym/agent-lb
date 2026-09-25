from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Integer, and_, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.time import to_utc_naive
from app.db.models import ApiKey, RequestLog

POOL_IDS = {
    "anthropic": "anthropic-general",
    "openai": "openai-codex",
    "glm": "glm",
    "kimi": "kimi",
    "openrouter": "openrouter",
}


@dataclass(frozen=True)
class ReceiptFilters:
    since: datetime | None = None
    until: datetime | None = None
    account_ids: list[str] | None = None
    api_key_ids: list[str] | None = None
    session_id: str | None = None
    provider: str | None = None
    pool: str | None = None
    models: list[str] | None = None
    status: str | None = None


class ReceiptsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def pool_expr():
        return case(
            *[(RequestLog.provider == provider, pool) for provider, pool in POOL_IDS.items()], else_=RequestLog.provider
        )

    @staticmethod
    def token_exprs():
        read = func.coalesce(RequestLog.cache_read_tokens, RequestLog.cached_input_tokens, 0)
        write = func.coalesce(RequestLog.cache_creation_tokens, 0)
        raw = func.coalesce(RequestLog.input_tokens, 0)
        uncached = case((RequestLog.provider == "anthropic", raw), else_=case((raw >= read, raw - read), else_=0))
        return uncached, func.coalesce(RequestLog.output_tokens, 0), read, write

    def conditions(self, filters: ReceiptFilters):
        clauses = [RequestLog.deleted_at.is_(None)]
        if filters.since is not None:
            clauses.append(RequestLog.requested_at >= to_utc_naive(filters.since))
        if filters.until is not None:
            clauses.append(RequestLog.requested_at < to_utc_naive(filters.until))
        if filters.account_ids:
            clauses.append(RequestLog.account_id.in_(filters.account_ids))
        if filters.api_key_ids:
            clauses.append(RequestLog.api_key_id.in_(filters.api_key_ids))
        if filters.session_id is not None:
            clauses.append(
                or_(RequestLog.session_id == filters.session_id, RequestLog.client_session_id == filters.session_id)
            )
        if filters.provider is not None:
            clauses.append(RequestLog.provider == filters.provider)
        if filters.pool is not None:
            clauses.append(self.pool_expr() == filters.pool)
        if filters.models:
            clauses.append(RequestLog.model.in_(filters.models))
        if filters.status:
            clauses.append(RequestLog.status == ("success" if filters.status == "ok" else "error"))
        return and_(*clauses)

    async def page(self, filters: ReceiptFilters, limit: int, offset: int):
        where = self.conditions(filters)
        total = int(
            (await self.session.execute(select(func.count()).select_from(RequestLog).where(where))).scalar_one()
        )
        rows = (
            await self.session.execute(
                select(RequestLog, ApiKey.name, ApiKey.key_prefix)
                .outerjoin(ApiKey, ApiKey.id == RequestLog.api_key_id)
                .where(where)
                .order_by(RequestLog.requested_at.desc(), RequestLog.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return rows, total

    @staticmethod
    def _metrics():
        inp, out, read, write = ReceiptsRepository.token_exprs()
        return (
            func.count().label("requests"),
            func.coalesce(func.sum(case((RequestLog.status != "success", 1), else_=0)), 0).label("errors"),
            func.coalesce(func.sum(inp), 0).label("input_tokens"),
            func.coalesce(func.sum(out), 0).label("output_tokens"),
            func.coalesce(func.sum(read), 0).label("cache_read_tokens"),
            func.coalesce(func.sum(write), 0).label("cache_write_tokens"),
            func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
        )

    async def aggregate(self, filters: ReceiptFilters, group_by: str, bucket_seconds: int):
        where = self.conditions(filters)
        totals = (
            await self.session.execute(
                select(
                    *self._metrics(),
                    func.min(RequestLog.requested_at).label("first"),
                    func.max(RequestLog.requested_at).label("last"),
                ).where(where)
            )
        ).one()
        group_expr = {
            "account": RequestLog.account_id,
            "pool": self.pool_expr(),
            "key": RequestLog.api_key_id,
            "session": RequestLog.session_id,
            "model": RequestLog.model,
            "provider": RequestLog.provider,
        }
        dialect = self.session.get_bind().dialect.name
        if dialect == "postgresql":
            epoch = cast(
                func.floor(func.extract("epoch", RequestLog.requested_at) / bucket_seconds) * bucket_seconds, Integer
            )
        else:
            epoch = (
                cast(cast(func.strftime("%s", RequestLog.requested_at), Integer) / bucket_seconds, Integer)
                * bucket_seconds
            )
        if group_by == "day":
            key = func.date(RequestLog.requested_at)
        else:
            key = group_expr[group_by]
        # Mixed-provider accounts have no single provider; never split a requested group.
        provider = case(
            (func.min(RequestLog.provider) == func.max(RequestLog.provider), func.min(RequestLog.provider)),
            else_=None,
        ).label("provider")
        group_columns = [key.label("key")]
        if group_by in ("account", "pool", "provider"):
            group_columns.append(provider)
        groups = (
            await self.session.execute(
                select(*group_columns, *self._metrics())
                .where(where)
                .group_by(key)
                .order_by(self._metrics()[-1].desc(), self._metrics()[0].desc())
                .limit(200)
            )
        ).all()
        series = (
            await self.session.execute(
                select(epoch.label("epoch"), RequestLog.provider, func.count().label("requests"))
                .where(where)
                .group_by(epoch, RequestLog.provider)
                .order_by(epoch)
            )
        ).all()
        latency_count = (
            await self.session.execute(select(func.count(RequestLog.latency_ms)).where(where))
        ).scalar_one()

        async def percentile(fraction: float):
            if not latency_count:
                return None
            index = int((latency_count - 1) * fraction + 0.999999999)  # nearest rank
            return (
                await self.session.execute(
                    select(RequestLog.latency_ms)
                    .where(where, RequestLog.latency_ms.is_not(None))
                    .order_by(RequestLog.latency_ms)
                    .limit(1)
                    .offset(index)
                )
            ).scalar_one()

        top_error = (
            await self.session.execute(
                select(RequestLog.error_code)
                .where(where, RequestLog.status != "success", RequestLog.error_code.is_not(None))
                .group_by(RequestLog.error_code)
                .order_by(func.count().desc(), RequestLog.error_code)
                .limit(1)
            )
        ).scalar_one_or_none()
        return totals, groups, series, await percentile(0.5), await percentile(0.95), top_error
