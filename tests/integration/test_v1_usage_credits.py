from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.utils.time import utcnow
from app.db.models import (
    ApiKeyLimit,
    LimitType,
    LimitWindow,
)
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import LimitRuleInput
from tests.integration.usage_api_helpers import _create_api_key as _create_api_key
from tests.integration.usage_api_helpers import _seed_upstream_usage as _seed_upstream_usage
from tests.integration.usage_api_helpers import _seed_upstream_usage_partial as _seed_upstream_usage_partial
from tests.integration.usage_api_helpers import _seed_upstream_usage_with_statuses as _seed_upstream_usage_with_statuses

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_v1_usage_returns_aggregate_credit_limits_when_upstream_usage_exists(async_client):
    _, plain_key = await _create_api_key(name="fallback-aggregate")
    now = utcnow()
    await _seed_upstream_usage(now=now)

    response = await async_client.get("/v1/usage", headers={"Authorization": f"Bearer {plain_key}"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["limits"] == []
    assert payload["upstream_limits"][0] == {
        "limit_type": "credits",
        "limit_window": "5h",
        "max_value": 450,
        "current_value": 68,
        "remaining_value": 382,
        "model_filter": None,
        "reset_at": payload["upstream_limits"][0]["reset_at"],
        "source": "aggregate",
    }
    assert payload["upstream_limits"][1] == {
        "limit_type": "credits",
        "limit_window": "7d",
        "max_value": 15120,
        "current_value": 3780,
        "remaining_value": 11340,
        "model_filter": None,
        "reset_at": payload["upstream_limits"][1]["reset_at"],
        "source": "aggregate",
    }
    assert payload["upstream_limits"][0]["reset_at"].endswith("Z")
    assert payload["upstream_limits"][1]["reset_at"].endswith("Z")


@pytest.mark.asyncio
async def test_v1_usage_returns_api_key_and_upstream_credit_limits_separately(async_client):
    key_id, plain_key = await _create_api_key(
        name="credit-override",
        limits=[
            LimitRuleInput(limit_type="credits", limit_window="5h", max_value=60),
            LimitRuleInput(limit_type="credits", limit_window="7d", max_value=1000),
        ],
    )
    now = utcnow()
    await _seed_upstream_usage(now=now)

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        await repo.replace_limits(
            key_id,
            [
                ApiKeyLimit(
                    api_key_id=key_id,
                    limit_type=LimitType.CREDITS,
                    limit_window=LimitWindow.FIVE_HOURS,
                    max_value=60,
                    current_value=999,
                    model_filter=None,
                    reset_at=now + timedelta(hours=5),
                ),
                ApiKeyLimit(
                    api_key_id=key_id,
                    limit_type=LimitType.CREDITS,
                    limit_window=LimitWindow.SEVEN_DAYS,
                    max_value=1000,
                    current_value=10,
                    model_filter=None,
                    reset_at=now + timedelta(days=7),
                ),
            ],
        )

    response = await async_client.get("/v1/usage", headers={"Authorization": f"Bearer {plain_key}"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["limits"] == [
        {
            "limit_type": "credits",
            "limit_window": "5h",
            "max_value": 60,
            "current_value": 60,
            "remaining_value": 0,
            "model_filter": None,
            "reset_at": payload["limits"][0]["reset_at"],
            "source": "api_key_limit",
        },
        {
            "limit_type": "credits",
            "limit_window": "7d",
            "max_value": 1000,
            "current_value": 10,
            "remaining_value": 990,
            "model_filter": None,
            "reset_at": payload["limits"][1]["reset_at"],
            "source": "api_key_limit",
        },
    ]
    assert [limit["source"] for limit in payload["upstream_limits"]] == ["aggregate", "aggregate"]
    assert [limit["limit_window"] for limit in payload["upstream_limits"]] == ["5h", "7d"]


@pytest.mark.asyncio
async def test_v1_usage_prefers_raw_limits_when_aggregate_credit_pair_is_partial(async_client):
    key_id, plain_key = await _create_api_key(
        name="fallback-partial-raw",
        limits=[
            LimitRuleInput(limit_type="total_tokens", limit_window="daily", max_value=300),
            LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=1000),
        ],
    )
    now = utcnow()
    await _seed_upstream_usage_partial(now=now, windows=("primary",))

    daily_reset = now + timedelta(hours=2)
    weekly_reset = now + timedelta(days=4)

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        await repo.replace_limits(
            key_id,
            [
                ApiKeyLimit(
                    api_key_id=key_id,
                    limit_type=LimitType.TOTAL_TOKENS,
                    limit_window=LimitWindow.DAILY,
                    max_value=300,
                    current_value=50,
                    model_filter=None,
                    reset_at=daily_reset,
                ),
                ApiKeyLimit(
                    api_key_id=key_id,
                    limit_type=LimitType.TOTAL_TOKENS,
                    limit_window=LimitWindow.WEEKLY,
                    max_value=1000,
                    current_value=200,
                    model_filter=None,
                    reset_at=weekly_reset,
                ),
            ],
        )

    response = await async_client.get("/v1/usage", headers={"Authorization": f"Bearer {plain_key}"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["limits"] == [
        {
            "limit_type": "total_tokens",
            "limit_window": "daily",
            "max_value": 300,
            "current_value": 50,
            "remaining_value": 250,
            "model_filter": None,
            "reset_at": daily_reset.isoformat() + "Z",
            "source": "api_key_limit",
        },
        {
            "limit_type": "total_tokens",
            "limit_window": "weekly",
            "max_value": 1000,
            "current_value": 200,
            "remaining_value": 800,
            "model_filter": None,
            "reset_at": weekly_reset.isoformat() + "Z",
            "source": "api_key_limit",
        },
    ]
    assert [limit["limit_window"] for limit in payload["upstream_limits"]] == ["5h"]


@pytest.mark.asyncio
async def test_v1_usage_falls_back_to_raw_credit_limits_when_aggregate_reset_is_missing(async_client):
    key_id, plain_key = await _create_api_key(
        name="fallback-missing-reset",
        limits=[
            LimitRuleInput(limit_type="credits", limit_window="5h", max_value=60),
            LimitRuleInput(limit_type="credits", limit_window="7d", max_value=1000),
        ],
    )
    now = utcnow()
    await _seed_upstream_usage_partial(now=now, windows=("primary", "secondary"), primary_reset_at=None)

    primary_reset = now + timedelta(hours=5)
    secondary_reset = now + timedelta(days=7)

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        await repo.replace_limits(
            key_id,
            [
                ApiKeyLimit(
                    api_key_id=key_id,
                    limit_type=LimitType.CREDITS,
                    limit_window=LimitWindow.FIVE_HOURS,
                    max_value=60,
                    current_value=12,
                    model_filter=None,
                    reset_at=primary_reset,
                ),
                ApiKeyLimit(
                    api_key_id=key_id,
                    limit_type=LimitType.CREDITS,
                    limit_window=LimitWindow.SEVEN_DAYS,
                    max_value=1000,
                    current_value=250,
                    model_filter=None,
                    reset_at=secondary_reset,
                ),
            ],
        )
        await session.commit()

    response = await async_client.get("/v1/usage", headers={"Authorization": f"Bearer {plain_key}"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["limits"] == [
        {
            "limit_type": "credits",
            "limit_window": "5h",
            "max_value": 60,
            "current_value": 12,
            "remaining_value": 48,
            "model_filter": None,
            "reset_at": primary_reset.isoformat() + "Z",
            "source": "api_key_limit",
        },
        {
            "limit_type": "credits",
            "limit_window": "7d",
            "max_value": 1000,
            "current_value": 250,
            "remaining_value": 750,
            "model_filter": None,
            "reset_at": secondary_reset.isoformat() + "Z",
            "source": "api_key_limit",
        },
    ]
    assert [limit["limit_window"] for limit in payload["upstream_limits"]] == ["7d"]


@pytest.mark.asyncio
async def test_v1_usage_ignores_paused_and_deactivated_accounts_in_aggregate_credit_windows(async_client):
    _, plain_key = await _create_api_key(name="aggregate-active-only")
    now = utcnow()
    await _seed_upstream_usage_with_statuses(now=now)

    response = await async_client.get("/v1/usage", headers={"Authorization": f"Bearer {plain_key}"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["limits"] == []
    assert payload["upstream_limits"] == [
        {
            "limit_type": "credits",
            "limit_window": "5h",
            "max_value": 225,
            "current_value": 45,
            "remaining_value": 180,
            "model_filter": None,
            "reset_at": payload["upstream_limits"][0]["reset_at"],
            "source": "aggregate",
        },
        {
            "limit_type": "credits",
            "limit_window": "7d",
            "max_value": 7560,
            "current_value": 1890,
            "remaining_value": 5670,
            "model_filter": None,
            "reset_at": payload["upstream_limits"][1]["reset_at"],
            "source": "aggregate",
        },
    ]
