from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.accounts import repository_state
from app.modules.accounts.repository_state import AccountRequestUsageSummary as AccountRequestUsageSummary
from app.modules.accounts.usage_rollup import (
    AccountUsageRollupRepository,
    UsageRollupSums,
    deduped_usage_aggregate_stmt,
)

_EMPTY_SUMS = UsageRollupSums(0, 0, 0, 0, 0.0)


def _add_sums(left: UsageRollupSums, right: UsageRollupSums) -> UsageRollupSums:
    return UsageRollupSums(
        request_count=left.request_count + right.request_count,
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        cached_input_tokens=left.cached_input_tokens + right.cached_input_tokens,
        cache_creation_tokens=left.cache_creation_tokens + right.cache_creation_tokens,
        cache_read_tokens=left.cache_read_tokens + right.cache_read_tokens,
        total_cost_usd=left.total_cost_usd + right.total_cost_usd,
    )


class AccountUsageRepository:
    _session: AsyncSession

    async def list_request_usage_summary_by_account(
        self, account_ids: list[str] | None = None
    ) -> dict[str, AccountRequestUsageSummary]:
        # Keep the established repository configuration surface while the
        # cache's mutable state lives in one module shared by every mixin.
        from app.modules.accounts import repository

        ttl_seconds = repository._SUMMARY_CACHE_TTL_SECONDS
        cache_key = tuple(sorted(account_ids)) if account_ids is not None else None
        generation = repository_state._summary_cache_generation
        if ttl_seconds > 0:
            cached = repository_state._cached_request_usage_summaries(cache_key)
            if cached is not None:
                return dict(cached)
        folded, watermark = await AccountUsageRollupRepository(self._session).read_state(account_ids)
        merged = dict(folded)
        tail = await self._session.execute(
            deduped_usage_aggregate_stmt(account_ids=account_ids, after_exclusive=watermark)
        )
        for (
            account_id,
            request_count,
            input_tokens,
            output_tokens,
            cached_input_tokens,
            cache_creation_tokens,
            cache_read_tokens,
            total_cost_usd,
        ) in tail.all():
            if account_id is None:
                continue
            sums = UsageRollupSums(
                request_count=int(request_count or 0),
                input_tokens=int(input_tokens or 0),
                output_tokens=int(output_tokens or 0),
                cached_input_tokens=int(cached_input_tokens or 0),
                cache_creation_tokens=int(cache_creation_tokens or 0),
                cache_read_tokens=int(cache_read_tokens or 0),
                total_cost_usd=float(total_cost_usd or 0.0),
            )
            merged[account_id] = _add_sums(merged.get(account_id, _EMPTY_SUMS), sums)
        summaries = {
            account_id: AccountRequestUsageSummary(
                request_count=sums.request_count,
                total_tokens=sums.input_tokens + sums.output_tokens,
                cached_input_tokens=max(0, min(sums.cached_input_tokens, sums.input_tokens)),
                cache_creation_tokens=sums.cache_creation_tokens,
                cache_read_tokens=sums.cache_read_tokens,
                total_cost_usd=round(sums.total_cost_usd, 6),
            )
            for account_id, sums in merged.items()
        }
        if ttl_seconds > 0:
            repository_state._store_request_usage_summaries(cache_key, summaries, ttl_seconds, generation)
        return dict(summaries)
