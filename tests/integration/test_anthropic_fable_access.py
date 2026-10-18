from __future__ import annotations

from datetime import timedelta, timezone

import pytest

from app.core.utils.time import utcnow
from tests.integration.anthropic_proxy_support import (
    _insert_account,
    _insert_fable_access_marker,
    _insert_fable_scoped_weekly,
    _insert_weekly_usage,
)
from tests.integration.anthropic_proxy_support import (
    fable_burn_first_enabled as fable_burn_first_enabled,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_over_threshold_account_with_fresh_capable_marker_joins_fable_pool(async_client):
    await _insert_account(
        account_id="anthropic-fable-probe-capable",
        provider="anthropic",
        access_token="anthropic-access-fable-probe-capable",
        email="fable-probe-capable@example.com",
    )
    await _insert_account(
        account_id="anthropic-fable-probe-unmarked",
        provider="anthropic",
        access_token="anthropic-access-fable-probe-unmarked",
        email="fable-probe-unmarked@example.com",
    )
    await _insert_weekly_usage(account_id="anthropic-fable-probe-capable", used_percent=60.0)
    await _insert_weekly_usage(account_id="anthropic-fable-probe-unmarked", used_percent=60.0)
    fresh_reset_at = int((utcnow() + timedelta(hours=6)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_fable_access_marker(
        account_id="anthropic-fable-probe-capable",
        used_percent=0.0,
        reset_at=fresh_reset_at,
    )

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-fable-probe-capable",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-fable-probe-capable"


@pytest.mark.asyncio
async def test_over_threshold_accounts_without_fresh_capable_marker_stay_excluded_from_fable_pool(async_client):
    for account_id in (
        "anthropic-fable-probe-control",
        "anthropic-fable-probe-refused",
        "anthropic-fable-probe-stale",
        "anthropic-fable-probe-nomarker",
    ):
        await _insert_account(
            account_id=account_id,
            provider="anthropic",
            access_token=f"anthropic-access-{account_id}",
            email=f"{account_id}@example.com",
        )
        await _insert_weekly_usage(account_id=account_id, used_percent=60.0)

    future_reset_at = int((utcnow() + timedelta(hours=6)).replace(tzinfo=timezone.utc).timestamp())
    past_reset_at = int((utcnow() - timedelta(hours=1)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_fable_access_marker(
        account_id="anthropic-fable-probe-control",
        used_percent=0.0,
        reset_at=future_reset_at,
    )
    await _insert_fable_access_marker(
        account_id="anthropic-fable-probe-refused",
        used_percent=100.0,
        reset_at=future_reset_at,
    )
    await _insert_fable_access_marker(
        account_id="anthropic-fable-probe-stale",
        used_percent=0.0,
        reset_at=past_reset_at,
    )

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-fable-probe-exclusions",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-fable-probe-control"


@pytest.mark.asyncio
async def test_non_fable_burn_preference_unchanged_by_capable_fable_marker(async_client, fable_burn_first_enabled):
    await _insert_account(
        account_id="anthropic-burn-marker-hot",
        provider="anthropic",
        access_token="anthropic-access-burn-marker-hot",
        email="burn-marker-hot@example.com",
    )
    await _insert_account(
        account_id="anthropic-burn-marker-fresh",
        provider="anthropic",
        access_token="anthropic-access-burn-marker-fresh",
        email="burn-marker-fresh@example.com",
    )
    await _insert_weekly_usage(account_id="anthropic-burn-marker-hot", used_percent=60.0)
    await _insert_weekly_usage(account_id="anthropic-burn-marker-fresh", used_percent=10.0)
    fresh_reset_at = int((utcnow() + timedelta(hours=6)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_fable_access_marker(
        account_id="anthropic-burn-marker-hot",
        used_percent=0.0,
        reset_at=fresh_reset_at,
    )

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-haiku-burn-marker",
            "model": "claude-haiku-4-5",
            "quotaKey": "anthropic_standard",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-burn-marker-hot"


@pytest.mark.asyncio
async def test_fable_scoped_exhaustion_excludes_despite_overall_headroom(async_client):

    await _insert_account(
        account_id="anthropic-scoped-exhausted",
        provider="anthropic",
        access_token="anthropic-access-scoped-exhausted",
        email="scoped-exhausted@example.com",
    )
    await _insert_account(
        account_id="anthropic-scoped-control",
        provider="anthropic",
        access_token="anthropic-access-scoped-control",
        email="scoped-control@example.com",
    )
    await _insert_weekly_usage(account_id="anthropic-scoped-exhausted", used_percent=30.0)
    await _insert_weekly_usage(account_id="anthropic-scoped-control", used_percent=10.0)
    scoped_reset_at = int((utcnow() + timedelta(days=2)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_fable_scoped_weekly(
        account_id="anthropic-scoped-exhausted",
        used_percent=100.0,
        reset_at=scoped_reset_at,
    )

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-fable-scoped-exhausted",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-scoped-control"
