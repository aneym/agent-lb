from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import RequestLog

_REPLAYED_REQUEST_ID_CHUNK = 500


@dataclass(frozen=True, slots=True)
class AccountRequestUsageSummary:
    request_count: int
    total_tokens: int
    cached_input_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    total_cost_usd: float


class AccountUsageRepository:
    _session: AsyncSession

    async def list_request_usage_summary_by_account(
        self,
        account_ids: list[str] | None = None,
    ) -> dict[str, AccountRequestUsageSummary]:

        conditions: list = [
            RequestLog.request_kind.not_in(("warmup", "limit_warmup")),
            RequestLog.deleted_at.is_(None),
        ]
        if account_ids:
            conditions.append(RequestLog.account_id.in_(account_ids))
        max_id = (await self._session.execute(select(func.max(RequestLog.id)))).scalar()
        if max_id is None:
            return {}
        conditions.append(RequestLog.id <= max_id)

        usage_sums = (
            func.count(RequestLog.id),
            func.coalesce(func.sum(RequestLog.input_tokens), 0),
            func.coalesce(func.sum(func.coalesce(RequestLog.output_tokens, RequestLog.reasoning_tokens, 0)), 0),
            func.coalesce(func.sum(RequestLog.cached_input_tokens), 0),
            func.coalesce(func.sum(RequestLog.cache_creation_tokens), 0),
            func.coalesce(func.sum(RequestLog.cache_read_tokens), 0),
            func.coalesce(func.sum(RequestLog.cost_usd), 0.0),
        )

        totals_stmt = select(RequestLog.account_id, *usage_sums).where(*conditions).group_by(RequestLog.account_id)

        totals: dict[str, list[int | float]] = {
            account_id: [int(value or 0) for value in counts] + [float(cost or 0.0)]
            for account_id, *counts, cost in (await self._session.execute(totals_stmt)).all()
            if account_id
        }

        replayed_request_ids_stmt = (
            select(RequestLog.request_id)
            .where(*conditions)
            .group_by(RequestLog.account_id, RequestLog.request_id, RequestLog.requested_at)
            .having(func.count(RequestLog.id) > 1)
        )
        replayed_request_ids = sorted(set((await self._session.execute(replayed_request_ids_stmt)).scalars()))
        newer = aliased(RequestLog)
        newer_conditions = [
            newer.request_kind.not_in(("warmup", "limit_warmup")),
            newer.deleted_at.is_(None),
            newer.id <= max_id,
        ]
        for offset in range(0, len(replayed_request_ids), _REPLAYED_REQUEST_ID_CHUNK):
            chunk = replayed_request_ids[offset : offset + _REPLAYED_REQUEST_ID_CHUNK]
            superseded_stmt = (
                select(RequestLog.account_id, *usage_sums)
                .where(
                    *conditions,
                    RequestLog.request_id.in_(chunk),
                    select(newer.id)
                    .where(
                        *newer_conditions,
                        newer.account_id == RequestLog.account_id,
                        newer.request_id == RequestLog.request_id,
                        newer.requested_at == RequestLog.requested_at,
                        newer.id > RequestLog.id,
                    )
                    .exists(),
                )
                .group_by(RequestLog.account_id)
            )
            for account_id, *counts, cost in (await self._session.execute(superseded_stmt)).all():
                account_totals = totals[account_id]
                for index, value in enumerate(counts):
                    account_totals[index] -= int(value or 0)
                account_totals[6] -= float(cost or 0.0)

        summaries: dict[str, AccountRequestUsageSummary] = {}
        for account_id, (
            request_count,
            input_tokens,
            output_tokens,
            cached_input_tokens,
            cache_creation_tokens,
            cache_read_tokens,
            total_cost_usd,
        ) in totals.items():
            input_sum = int(input_tokens)
            cached_sum = max(0, min(int(cached_input_tokens), input_sum))
            summaries[account_id] = AccountRequestUsageSummary(
                request_count=int(request_count),
                total_tokens=input_sum + int(output_tokens),
                cached_input_tokens=cached_sum,
                cache_creation_tokens=int(cache_creation_tokens),
                cache_read_tokens=int(cache_read_tokens),
                total_cost_usd=round(float(total_cost_usd), 6),
            )
        return summaries
