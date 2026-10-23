from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Integer, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RequestUsageHourlyRollup
from app.db.request_models import RequestLog
from app.modules.accounts.usage_rollup import api_key_usage_aggregate_stmt, read_api_key_rollup_state
from app.modules.accounts.usage_time_rollup import HOURLY_BUCKET_SECONDS, WARMUP_REQUEST_KINDS, to_dimension
from app.modules.accounts.usage_time_rollup_read import RawWindow, raw_windows_clause, read_hourly_window
from app.modules.api_keys.repository_data import (
    ApiKeyAccountCost,
    ApiKeyTrendBucket,
    ApiKeyUsageSummary,
    ApiKeyUsageTotals,
)
from app.modules.api_keys.usage_window import read_api_key_usage_window
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
                ApiKeyAccountCost(account_id=None, email=None, cost_usd=round(deleted_cost, 6), is_deleted=True)
            )
        account_costs.sort(key=lambda item: item.cost_usd, reverse=True)
        return account_costs

    @staticmethod
    def _exclude_warmup_clause():
        return and_(RequestLog.request_kind.not_in(("warmup", "limit_warmup")), exclude_forwarded_edge_logs())

    async def list_usage_summary_by_key(self, api_key_ids: list[str] | None = None) -> dict[str, ApiKeyUsageSummary]:
        folded, watermark = await read_api_key_rollup_state(self._session, api_key_ids)
        merged: dict[str, list[float]] = {
            key_id: [
                sums.request_count,
                sums.input_tokens,
                sums.output_tokens,
                sums.cached_input_tokens,
                sums.total_cost_usd,
            ]
            for key_id, sums in folded.items()
        }
        tail_stmt = api_key_usage_aggregate_stmt(api_key_ids=api_key_ids, after_exclusive=watermark)
        result = await self._session.execute(tail_stmt)
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
            totals = merged.setdefault(api_key_id, [0, 0, 0, 0, 0.0])
            totals[0] += int(request_count or 0)
            totals[1] += int(input_tokens or 0)
            totals[2] += int(output_tokens or 0)
            totals[3] += int(cached_input_tokens or 0)
            totals[4] += float(total_cost_usd or 0.0)

        summaries: dict[str, ApiKeyUsageSummary] = {}
        for api_key_id, (request_count, input_sum, output_sum, cached_sum, total_cost_usd) in merged.items():
            input_total = int(input_sum)
            output_total = int(output_sum)
            cached_total = max(0, min(int(cached_sum), input_total))
            summaries[api_key_id] = ApiKeyUsageSummary(
                request_count=int(request_count),
                total_tokens=input_total + output_total,
                cached_input_tokens=cached_total,
                total_cost_usd=round(float(total_cost_usd), 6),
            )
        return summaries

    async def get_usage_summary_by_key_id(self, key_id: str) -> ApiKeyUsageSummary:
        """Return aggregate usage totals for a single API key (zeroes if no logs)."""
        summaries = await self.list_usage_summary_by_key([key_id])
        return summaries.get(
            key_id,
            ApiKeyUsageSummary(request_count=0, total_tokens=0, cached_input_tokens=0, total_cost_usd=0.0),
        )

    async def usage_7d_by_account(self, key_id: str, since: datetime, until: datetime) -> list[ApiKeyAccountCost]:
        return (await read_api_key_usage_window(self._session, key_id, since, until)).account_costs

    async def trends_by_key(
        self,
        key_id: str,
        since: datetime,
        until: datetime,
        bucket_seconds: int = 3600,
    ) -> list[ApiKeyTrendBucket]:
        # Folded history from the hourly rollups (the api_key_id dimension
        # and the output-or-reasoning measure were folded for exactly this
        # read); raw only covers the un-folded complement. Non-hour-multiple
        # bucket sizes degrade to the full raw scan.
        merged: dict[int, list[float]] = {}

        def _add(bucket_epoch: int, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
            entry = merged.setdefault(bucket_epoch, [0, 0, 0.0])
            entry[0] += input_tokens
            entry[1] += output_tokens
            entry[2] += cost_usd

        raw_windows: list[RawWindow] = [(since, until)]
        if bucket_seconds > 0 and bucket_seconds % HOURLY_BUCKET_SECONDS == 0:
            rollup_rows, raw_windows = await read_hourly_window(
                self._session,
                since,
                until,
                filters=(
                    RequestUsageHourlyRollup.api_key_id == to_dimension(key_id),
                    RequestUsageHourlyRollup.request_kind.not_in(WARMUP_REQUEST_KINDS),
                ),
            )
            for rollup in rollup_rows:
                _add(
                    rollup.bucket_epoch // bucket_seconds * bucket_seconds,
                    rollup.input_tokens,
                    rollup.output_or_reasoning_tokens,
                    rollup.cost_usd,
                )
        if raw_windows:
            bind = self._session.get_bind()
            dialect = bind.dialect.name if bind else "sqlite"
            if dialect == "postgresql":
                bucket_expr = (
                    func.floor(func.extract("epoch", RequestLog.requested_at) / bucket_seconds) * bucket_seconds
                )
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
                    raw_windows_clause(raw_windows),
                    self._exclude_warmup_clause(),
                )
                .group_by(bucket_col)
            )
            for row in (await self._session.execute(stmt)).all():
                _add(
                    int(row.bucket_epoch),
                    int(row.total_input_tokens or 0),
                    int(row.total_output_tokens or 0),
                    float(row.total_cost_usd or 0.0),
                )
        return [
            ApiKeyTrendBucket(
                bucket_epoch=bucket_epoch,
                total_tokens=int(entry[0] + entry[1]),
                total_cost_usd=round(float(entry[2]), 6),
            )
            for bucket_epoch, entry in sorted(merged.items())
        ]

    async def usage_7d(self, key_id: str, since: datetime, until: datetime) -> ApiKeyUsageTotals:
        return await read_api_key_usage_window(self._session, key_id, since, until)
