from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Integer, and_, cast, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account
from app.db.request_models import RequestLog
from app.modules.api_keys.repository_data import ApiKeyAccountCost, ApiKeyTrendBucket, ApiKeyUsageSummary, ApiKeyUsageTotals  # noqa: I001  # fmt: skip
from app.modules.request_logs.edge_source import exclude_forwarded_edge_logs


class ApiKeyUsageQueries:
    _session: AsyncSession

    @staticmethod
    def _build_account_costs(rows: Sequence[object]) -> list[ApiKeyAccountCost]:
        account_costs: list[ApiKeyAccountCost] = []
        deleted_cost = 0.0

        for row in rows:
            cost = round(float(getattr(row, "cost_usd", 0.0) or 0.0), 6)
            if cost <= 0:
                continue
            is_deleted = bool(getattr(row, "is_deleted", False))
            if is_deleted:
                deleted_cost += cost
                continue
            account_costs.append(
                ApiKeyAccountCost(
                    account_id=getattr(row, "account_id", None),
                    email=getattr(row, "email", None),
                    cost_usd=cost,
                    is_deleted=False,
                )
            )

        if deleted_cost > 0:
            account_costs.append(
                ApiKeyAccountCost(
                    account_id=None,
                    email=None,
                    cost_usd=round(deleted_cost, 6),
                    is_deleted=True,
                )
            )

        account_costs.sort(key=lambda item: item.cost_usd, reverse=True)
        return account_costs

    @staticmethod
    def _exclude_warmup_clause():
        return and_(
            RequestLog.request_kind.not_in(("warmup", "limit_warmup")),
            exclude_forwarded_edge_logs(),
        )

    async def list_usage_summary_by_key(self) -> dict[str, ApiKeyUsageSummary]:
        stmt = (
            select(
                RequestLog.api_key_id,
                func.count(RequestLog.id).label("request_count"),
                func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
                func.coalesce(
                    func.sum(func.coalesce(RequestLog.output_tokens, RequestLog.reasoning_tokens, 0)),
                    0,
                ).label("output_tokens"),
                func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("total_cost_usd"),
            )
            .where(RequestLog.api_key_id.is_not(None), self._exclude_warmup_clause())
            .group_by(RequestLog.api_key_id)
        )
        result = await self._session.execute(stmt)
        summaries: dict[str, ApiKeyUsageSummary] = {}
        for (
            api_key_id,
            request_count,
            input_tokens,
            output_tokens,
            cached_input_tokens,
            total_cost_usd,
        ) in result.all():
            if not api_key_id:
                continue
            input_sum = int(input_tokens or 0)
            output_sum = int(output_tokens or 0)
            cached_sum = int(cached_input_tokens or 0)
            cached_sum = max(0, min(cached_sum, input_sum))
            summaries[api_key_id] = ApiKeyUsageSummary(
                request_count=int(request_count or 0),
                total_tokens=input_sum + output_sum,
                cached_input_tokens=cached_sum,
                total_cost_usd=round(float(total_cost_usd or 0.0), 6),
            )

        return summaries

    async def get_usage_summary_by_key_id(self, key_id: str) -> ApiKeyUsageSummary:

        stmt = select(
            func.count(RequestLog.id).label("request_count"),
            func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
            func.coalesce(
                func.sum(func.coalesce(RequestLog.output_tokens, RequestLog.reasoning_tokens, 0)),
                0,
            ).label("output_tokens"),
            func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
            func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("total_cost_usd"),
        ).where(
            RequestLog.api_key_id == key_id,
            self._exclude_warmup_clause(),
        )
        result = await self._session.execute(stmt)
        row = result.one()
        input_sum = int(row.input_tokens or 0)
        output_sum = int(row.output_tokens or 0)
        cached_sum = int(row.cached_input_tokens or 0)
        cached_sum = max(0, min(cached_sum, input_sum))
        return ApiKeyUsageSummary(
            request_count=int(row.request_count or 0),
            total_tokens=input_sum + output_sum,
            cached_input_tokens=cached_sum,
            total_cost_usd=round(float(row.total_cost_usd or 0.0), 6),
        )

    async def usage_7d_by_account(
        self,
        key_id: str,
        since: datetime,
        until: datetime,
    ) -> list[ApiKeyAccountCost]:
        deleted_expr = func.coalesce(RequestLog.deleted_at.is_not(None), False)
        stmt = (
            select(
                RequestLog.account_id,
                Account.email,
                deleted_expr.label("is_deleted"),
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
            )
            .outerjoin(Account, Account.id == RequestLog.account_id)
            .where(
                RequestLog.api_key_id == key_id,
                RequestLog.requested_at >= since,
                RequestLog.requested_at < until,
                self._exclude_warmup_clause(),
            )
            .group_by(RequestLog.account_id, Account.email, deleted_expr)
        )
        result = await self._session.execute(stmt)
        return self._build_account_costs(result.all())

    async def trends_by_key(
        self,
        key_id: str,
        since: datetime,
        until: datetime,
        bucket_seconds: int = 3600,
    ) -> list[ApiKeyTrendBucket]:
        bind = self._session.get_bind()
        dialect = bind.dialect.name if bind else "sqlite"
        if dialect == "postgresql":
            bucket_expr = func.floor(func.extract("epoch", RequestLog.requested_at) / bucket_seconds) * bucket_seconds
        else:
            epoch_col = cast(func.strftime("%s", RequestLog.requested_at), Integer)
            bucket_expr = cast(epoch_col / bucket_seconds, Integer) * bucket_seconds
        bucket_col = bucket_expr.label("bucket_epoch")

        stmt = (
            select(
                bucket_col,
                func.coalesce(func.sum(RequestLog.input_tokens), 0).label("total_input_tokens"),
                func.coalesce(
                    func.sum(func.coalesce(RequestLog.output_tokens, RequestLog.reasoning_tokens, 0)),
                    0,
                ).label("total_output_tokens"),
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("total_cost_usd"),
            )
            .where(
                RequestLog.api_key_id == key_id,
                RequestLog.requested_at >= since,
                RequestLog.requested_at < until,
                self._exclude_warmup_clause(),
            )
            .group_by(bucket_col)
            .order_by(bucket_col)
        )
        result = await self._session.execute(stmt)
        return [
            ApiKeyTrendBucket(
                bucket_epoch=int(row.bucket_epoch),
                total_tokens=int((row.total_input_tokens or 0) + (row.total_output_tokens or 0)),
                total_cost_usd=round(float(row.total_cost_usd or 0.0), 6),
            )
            for row in result.all()
        ]

    async def usage_7d(self, key_id: str, since: datetime, until: datetime) -> ApiKeyUsageTotals:
        filtered_logs = (
            select(
                RequestLog.id.label("id"),
                RequestLog.account_id.label("account_id"),
                RequestLog.deleted_at.label("deleted_at"),
                RequestLog.input_tokens.label("input_tokens"),
                RequestLog.output_tokens.label("output_tokens"),
                RequestLog.reasoning_tokens.label("reasoning_tokens"),
                RequestLog.cached_input_tokens.label("cached_input_tokens"),
                RequestLog.cost_usd.label("cost_usd"),
            )
            .where(
                RequestLog.api_key_id == key_id,
                RequestLog.requested_at >= since,
                RequestLog.requested_at < until,
                self._exclude_warmup_clause(),
            )
            .cte("filtered_logs")
        )
        usage_totals = select(
            func.count(filtered_logs.c.id).label("total_requests"),
            func.coalesce(func.sum(filtered_logs.c.input_tokens), 0).label("total_input_tokens"),
            func.coalesce(
                func.sum(func.coalesce(filtered_logs.c.output_tokens, filtered_logs.c.reasoning_tokens, 0)),
                0,
            ).label("total_output_tokens"),
            func.coalesce(func.sum(filtered_logs.c.cached_input_tokens), 0).label("cached_input_tokens"),
            func.coalesce(func.sum(filtered_logs.c.cost_usd), 0.0).label("total_cost_usd"),
        ).cte("usage_totals")
        deleted_expr = func.coalesce(filtered_logs.c.deleted_at.is_not(None), False)
        usage_grouped = (
            select(
                filtered_logs.c.account_id.label("account_id"),
                Account.email.label("email"),
                deleted_expr.label("is_deleted"),
                func.coalesce(func.sum(filtered_logs.c.cost_usd), 0.0).label("cost_usd"),
            )
            .select_from(filtered_logs.outerjoin(Account, Account.id == filtered_logs.c.account_id))
            .group_by(filtered_logs.c.account_id, Account.email, deleted_expr)
            .cte("usage_grouped")
        )
        stmt = select(
            usage_totals.c.total_requests,
            usage_totals.c.total_input_tokens,
            usage_totals.c.total_output_tokens,
            usage_totals.c.cached_input_tokens,
            usage_totals.c.total_cost_usd,
            usage_grouped.c.account_id,
            usage_grouped.c.email,
            usage_grouped.c.is_deleted,
            usage_grouped.c.cost_usd,
        ).select_from(usage_totals.outerjoin(usage_grouped, true()))
        result = await self._session.execute(stmt)
        rows = result.all()
        row = rows[0]
        input_sum = int(row.total_input_tokens or 0)
        output_sum = int(row.total_output_tokens or 0)
        cached_sum = int(row.cached_input_tokens or 0)
        cached_sum = max(0, min(cached_sum, input_sum))
        return ApiKeyUsageTotals(
            total_requests=int(row.total_requests),
            total_tokens=input_sum + output_sum,
            cached_input_tokens=cached_sum,
            total_cost_usd=round(float(row.total_cost_usd or 0.0), 6),
            account_costs=self._build_account_costs(rows),
        )
