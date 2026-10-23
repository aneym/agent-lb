from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, RequestUsageHourlyRollup
from app.db.request_models import RequestLog
from app.modules.accounts.usage_time_rollup import WARMUP_REQUEST_KINDS, from_dimension, to_dimension
from app.modules.accounts.usage_time_rollup_read import raw_windows_clause, read_hourly_window
from app.modules.api_keys.repository_data import ApiKeyAccountCost, ApiKeyUsageTotals
from app.modules.request_logs.edge_source import exclude_forwarded_edge_logs


@dataclass(slots=True)
class _WindowSums:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, requests: int, input_tokens: int, output_tokens: int, cached_tokens: int, cost_usd: float) -> None:
        self.requests += requests
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cached_tokens += cached_tokens
        self.cost_usd += cost_usd


async def read_api_key_usage_window(
    session: AsyncSession, key_id: str, since: datetime, until: datetime
) -> ApiKeyUsageTotals:
    folded, raw_windows = await read_hourly_window(
        session,
        since,
        until,
        filters=(
            RequestUsageHourlyRollup.api_key_id == to_dimension(key_id),
            RequestUsageHourlyRollup.request_kind.not_in(WARMUP_REQUEST_KINDS),
        ),
    )
    totals = _WindowSums()
    costs: dict[tuple[str | None, bool], float] = {}
    for row in folded:
        totals.add(
            row.request_count,
            row.input_tokens,
            row.output_or_reasoning_tokens,
            row.cached_input_tokens,
            row.cost_usd,
        )
        group = (from_dimension(row.account_id), row.is_deleted)
        costs[group] = costs.get(group, 0.0) + row.cost_usd
    if raw_windows:
        deleted = RequestLog.deleted_at.is_not(None)
        stmt = (
            select(
                RequestLog.account_id,
                deleted,
                func.count(RequestLog.id),
                func.coalesce(func.sum(RequestLog.input_tokens), 0),
                func.coalesce(func.sum(func.coalesce(RequestLog.output_tokens, RequestLog.reasoning_tokens, 0)), 0),
                func.coalesce(func.sum(RequestLog.cached_input_tokens), 0),
                func.coalesce(func.sum(RequestLog.cost_usd), 0.0),
            )
            .where(
                RequestLog.api_key_id == key_id,
                raw_windows_clause(raw_windows),
                RequestLog.request_kind.not_in(WARMUP_REQUEST_KINDS),
                exclude_forwarded_edge_logs(),
            )
            .group_by(RequestLog.account_id, deleted)
        )
        for account_id, is_deleted, requests, inputs, outputs, cached, cost in (await session.execute(stmt)).all():
            totals.add(int(requests), int(inputs), int(outputs), int(cached), float(cost))
            group = (account_id, bool(is_deleted))
            costs[group] = costs.get(group, 0.0) + float(cost)
    account_ids = {account_id for account_id, is_deleted in costs if account_id is not None and not is_deleted}
    emails = dict((await session.execute(select(Account.id, Account.email).where(Account.id.in_(account_ids)))).all())
    account_costs: list[ApiKeyAccountCost] = []
    deleted_cost = 0.0
    for (account_id, is_deleted), raw_cost in costs.items():
        cost = round(raw_cost, 6)
        if cost <= 0:
            continue
        if is_deleted:
            deleted_cost += cost
        else:
            account_costs.append(ApiKeyAccountCost(account_id, emails.get(account_id), cost))
    if deleted_cost > 0:
        account_costs.append(ApiKeyAccountCost(None, None, round(deleted_cost, 6), is_deleted=True))
    account_costs.sort(key=lambda item: item.cost_usd, reverse=True)
    return ApiKeyUsageTotals(
        total_requests=totals.requests,
        total_tokens=totals.input_tokens + totals.output_tokens,
        cached_input_tokens=max(0, min(totals.cached_tokens, totals.input_tokens)),
        total_cost_usd=round(totals.cost_usd, 6),
        account_costs=account_costs,
    )
