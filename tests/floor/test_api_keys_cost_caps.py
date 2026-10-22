from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.db.request_models import LimitType
from app.modules.api_keys.repository_data import ApiKeyTrendBucket
from app.modules.api_keys.service import ApiKeysService
from app.modules.api_keys.service_data import ApiKeyRequestUsageBudget
from app.modules.api_keys.usage_calculations import _build_api_key_trends
from tests.floor.api_keys_service_support import _cost_capped_key, _FakeApiKeysRepository

pytestmark = pytest.mark.unit


def test_build_api_key_trends_includes_partial_boundary_hours() -> None:
    since = datetime(2026, 3, 23, 10, 37, 0)
    until = datetime(2026, 3, 30, 10, 37, 0)
    oldest_bucket = datetime(2026, 3, 23, 10, 0, 0, tzinfo=timezone.utc)
    newest_bucket = datetime(2026, 3, 30, 10, 0, 0, tzinfo=timezone.utc)

    trends = _build_api_key_trends(
        "key-123",
        [
            ApiKeyTrendBucket(bucket_epoch=int(oldest_bucket.timestamp()), total_tokens=5, total_cost_usd=0.1),
            ApiKeyTrendBucket(bucket_epoch=int(newest_bucket.timestamp()), total_tokens=7, total_cost_usd=0.2),
        ],
        since,
        until,
        bucket_seconds=3600,
    )

    assert len(trends.cost) == 169
    assert len(trends.tokens) == 169
    assert trends.cost[0].t == oldest_bucket
    assert trends.cost[-1].t == newest_bucket
    assert sum(point.v for point in trends.tokens) == pytest.approx(12.0)
    assert sum(point.v for point in trends.cost) == pytest.approx(0.3)


def test_build_api_key_trends_keeps_aligned_windows_at_168_buckets() -> None:
    since = datetime(2026, 3, 23, 11, 0, 0)
    until = datetime(2026, 3, 30, 11, 0, 0)
    oldest_bucket = datetime(2026, 3, 23, 11, 0, 0, tzinfo=timezone.utc)
    newest_bucket = datetime(2026, 3, 30, 10, 0, 0, tzinfo=timezone.utc)

    trends = _build_api_key_trends(
        "key-123",
        [
            ApiKeyTrendBucket(bucket_epoch=int(oldest_bucket.timestamp()), total_tokens=5, total_cost_usd=0.1),
            ApiKeyTrendBucket(bucket_epoch=int(newest_bucket.timestamp()), total_tokens=7, total_cost_usd=0.2),
        ],
        since,
        until,
        bucket_seconds=3600,
    )

    assert len(trends.cost) == 168
    assert len(trends.tokens) == 168
    assert trends.cost[0].t == oldest_bucket
    assert trends.cost[-1].t == newest_bucket
    assert sum(point.v for point in trends.tokens) == pytest.approx(12.0)
    assert sum(point.v for point in trends.cost) == pytest.approx(0.3)


@pytest.mark.parametrize(
    ("model", "settle_kwargs", "expected_microdollars"),
    [
        (
            "claude-opus-5-5[1m]",
            {"cache_creation_input_tokens": 2_000, "cache_read_input_tokens": 3_000, "cache_creation_tier": "5m"},
            4_000 + 2_000 + 10_000 + 600,
        ),
        (
            "claude-opus-5-5[1m]",
            {"cache_creation_input_tokens": 2_000, "cache_read_input_tokens": 3_000, "cache_creation_tier": "1h"},
            4_000 + 2_000 + 16_000 + 600,
        ),
        ("claude-opus-5-5", {"cached_input_tokens": 3_000}, 4_000 + 2_000 + 600),
        ("claude-haiku-4-5-20251001", {}, 1_000 + 500),
        (
            "claude-opus-5-5",
            {
                "cache_creation_input_tokens": 2_000,
                "cache_read_input_tokens": 3_000,
                "cache_creation_1h_input_tokens": 500,
            },
            4_000 + 2_000 + 7_500 + 4_000 + 600,
        ),
        ("claude-opus-4-6[1m]", {}, 5_000 + 2_500),
    ],
)
async def test_cost_cap_settles_claude_usage_at_list_price(
    model: str, settle_kwargs: dict[str, Any], expected_microdollars: int
) -> None:

    repo = _FakeApiKeysRepository()
    service = ApiKeysService(repo)
    key_id = await _cost_capped_key(service, "claude-list-price")
    reservation = await service.enforce_limits_for_request(
        key_id,
        request_model=model,
        request_usage_budget=ApiKeyRequestUsageBudget(input_tokens=0, output_tokens=0),
    )

    await service.finalize_usage_reservation(
        reservation.reservation_id, model=model, input_tokens=1_000, output_tokens=100, **settle_kwargs
    )

    limits = await repo.get_limits_by_key(key_id)
    cost_limit = next(limit for limit in limits if limit.limit_type == LimitType.COST_USD)
    assert cost_limit.current_value == expected_microdollars
