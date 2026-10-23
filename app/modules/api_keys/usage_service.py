from __future__ import annotations

from datetime import timedelta

from app.modules.api_keys.key_validation import _lazy_reset_expired_limits
from app.modules.api_keys.repository_protocol import ApiKeysRepositoryProtocol
from app.modules.api_keys.service_data import (
    _DETAIL_BUCKET_SECONDS,
    _SPARKLINE_DAYS,
    ApiKeyAccountCostData,
    ApiKeySelfLimitData,
    ApiKeySelfUsageData,
    ApiKeyTrendsData,
    ApiKeyUsage7DayData,
)
from app.modules.api_keys.usage_calculations import _build_api_key_trends


class ApiKeyUsageService:
    _repository: ApiKeysRepositoryProtocol

    async def get_key_trends(self, key_id: str) -> ApiKeyTrendsData | None:
        row = await self._repository.get_by_id(key_id)
        if row is None:
            return None
        now = self._now()
        since = now - timedelta(days=_SPARKLINE_DAYS)
        buckets = await self._repository.trends_by_key(key_id, since, now, _DETAIL_BUCKET_SECONDS)
        return _build_api_key_trends(key_id, buckets, since, now, _DETAIL_BUCKET_SECONDS)

    async def get_key_usage_summary_for_self(self, key_id: str) -> ApiKeySelfUsageData | None:
        row = await self._repository.get_by_id(key_id)
        if row is None:
            return None
        now = self._now()
        limits_reset = await _lazy_reset_expired_limits(self._repository, row.limits, now=now)
        refreshed = await self._repository.get_by_id(key_id) if limits_reset else row
        if refreshed is None:
            return None
        usage = await self._repository.get_usage_summary_by_key_id(key_id)
        limits = [
            ApiKeySelfLimitData(
                limit_type=limit.limit_type.value,
                limit_window=limit.limit_window.value,
                max_value=limit.max_value,
                current_value=max(0, min(limit.current_value, limit.max_value)),
                remaining_value=max(0, limit.max_value - max(0, min(limit.current_value, limit.max_value))),
                model_filter=limit.model_filter,
                reset_at=limit.reset_at,
                source="api_key_limit",
            )
            for limit in refreshed.limits
        ]
        return ApiKeySelfUsageData(
            request_count=usage.request_count,
            total_tokens=usage.total_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            total_cost_usd=usage.total_cost_usd,
            limits=limits,
        )

    async def get_key_usage_7d(self, key_id: str) -> ApiKeyUsage7DayData | None:
        row = await self._repository.get_by_id(key_id)
        if row is None:
            return None
        now = self._now()
        since = now - timedelta(days=7)
        data = await self._repository.usage_7d(key_id, since, now)
        return ApiKeyUsage7DayData(
            key_id=key_id,
            total_tokens=data.total_tokens,
            total_cost_usd=data.total_cost_usd,
            total_requests=data.total_requests,
            cached_input_tokens=data.cached_input_tokens,
            account_costs=[
                ApiKeyAccountCostData(
                    account_id=ac.account_id, email=ac.email, cost_usd=ac.cost_usd, is_deleted=ac.is_deleted
                )
                for ac in data.account_costs
            ],
        )
