from __future__ import annotations

from datetime import timedelta, timezone

import pytest

import app.modules.proxy.anthropic_service as anthropic_proxy_module
from app.core.utils.time import utcnow
from app.db.models import AccountStatus
from app.dependencies import _proxy_repo_context
from tests.integration.anthropic_proxy_support import (
    ANTHROPIC_SSE_BYTES,
    _FakeResponse,
    _FakeResponseContext,
    _insert_account,
    _insert_fable_scoped_weekly,
    _insert_primary_usage,
    _insert_quota_cooldown,
    _insert_weekly_usage,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_fable_scoped_exhaustion_with_elapsed_reset_remains_in_model_scope(
    async_client,
    monkeypatch,
):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE", "true")
    get_settings.cache_clear()

    await _insert_account(
        account_id="anthropic-fable-elapsed-scoped-headroom",
        provider="anthropic",
        access_token="anthropic-access-elapsed-scoped-headroom",
        email="elapsed-scoped-headroom@example.com",
    )
    await _insert_account(
        account_id="anthropic-fable-elapsed-scoped-paid",
        provider="anthropic",
        access_token="anthropic-access-elapsed-scoped-paid",
        email="elapsed-scoped-paid@example.com",
        status=AccountStatus.RATE_LIMITED,
    )
    now = utcnow()
    past_reset_at = int((now - timedelta(hours=1)).replace(tzinfo=timezone.utc).timestamp())
    paid_reset_at = int((now + timedelta(hours=1)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_primary_usage(
        account_id="anthropic-fable-elapsed-scoped-headroom",
        used_percent=10.0,
    )
    await _insert_weekly_usage(
        account_id="anthropic-fable-elapsed-scoped-headroom",
        used_percent=10.0,
    )
    await _insert_fable_scoped_weekly(
        account_id="anthropic-fable-elapsed-scoped-headroom",
        used_percent=100.0,
        reset_at=past_reset_at,
    )
    await _insert_primary_usage(
        account_id="anthropic-fable-elapsed-scoped-paid",
        used_percent=100.0,
        reset_at=paid_reset_at,
        credits_has=True,
        credits_balance=25.0,
    )
    await _insert_weekly_usage(
        account_id="anthropic-fable-elapsed-scoped-paid",
        used_percent=20.0,
    )
    await _insert_fable_scoped_weekly(
        account_id="anthropic-fable-elapsed-scoped-paid",
        used_percent=20.0,
        reset_at=paid_reset_at,
    )
    await _insert_quota_cooldown(
        account_id="anthropic-fable-elapsed-scoped-paid",
        quota_key=anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
        reset_at=paid_reset_at,
    )

    eligibility = await anthropic_proxy_module.AnthropicProxyService(_proxy_repo_context)._provider_quota_eligibility(
        "anthropic",
        "anthropic_top_thinking",
        model="claude-fable-5",
    )

    assert eligibility.account_ids == ["anthropic-fable-elapsed-scoped-headroom"]
    assert eligibility.paid_fallback_account_ids == frozenset()

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-fable-elapsed-scoped-headroom",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-fable-elapsed-scoped-headroom"


@pytest.mark.asyncio
async def test_all_fable_scoped_snapshot_exhaustions_still_attempt_upstream(
    async_client,
    monkeypatch,
):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE", "true")
    get_settings.cache_clear()

    now = utcnow()
    earliest_reset = int((now + timedelta(hours=2)).replace(tzinfo=timezone.utc).timestamp())
    later_reset = earliest_reset + 3600
    for account_id, reset_at in (
        ("anthropic-fable-hard-excluded-a", earliest_reset),
        ("anthropic-fable-hard-excluded-b", later_reset),
    ):
        await _insert_account(
            account_id=account_id,
            provider="anthropic",
            access_token=f"anthropic-access-{account_id}",
            email=f"{account_id}@example.com",
        )
        await _insert_primary_usage(account_id=account_id, used_percent=10.0)
        await _insert_weekly_usage(account_id=account_id, used_percent=10.0)
        await _insert_fable_scoped_weekly(
            account_id=account_id,
            used_percent=100.0,
            reset_at=reset_at,
        )

    eligibility = await anthropic_proxy_module.AnthropicProxyService(_proxy_repo_context)._provider_quota_eligibility(
        "anthropic",
        "anthropic_top_thinking",
        model="claude-fable-5",
    )

    assert eligibility.account_ids == ["anthropic-fable-hard-excluded-a", "anthropic-fable-hard-excluded-b"]
    assert eligibility.blocked_count == 0
    assert eligibility.next_reset_at is None
    assert eligibility.paid_fallback_account_ids == frozenset()

    upstream_calls: list[str] = []

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, json_body
        upstream_calls.append(headers["Authorization"])
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_SSE_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 200
    assert len(upstream_calls) == 1


@pytest.mark.asyncio
async def test_fable_scoped_headroom_admits_despite_overall_heuristic(async_client):

    await _insert_account(
        account_id="anthropic-scoped-headroom",
        provider="anthropic",
        access_token="anthropic-access-scoped-headroom",
        email="scoped-headroom@example.com",
    )
    await _insert_weekly_usage(account_id="anthropic-scoped-headroom", used_percent=62.0)
    await _insert_fable_scoped_weekly(account_id="anthropic-scoped-headroom", used_percent=45.0)

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-fable-scoped-headroom",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-scoped-headroom"


@pytest.mark.asyncio
async def test_non_fable_burn_set_derived_from_scoped_percents_when_fresh(async_client):

    await _insert_account(
        account_id="anthropic-burn-scoped-hot",
        provider="anthropic",
        access_token="anthropic-access-burn-scoped-hot",
        email="burn-scoped-hot@example.com",
    )
    await _insert_account(
        account_id="anthropic-burn-scoped-cool",
        provider="anthropic",
        access_token="anthropic-access-burn-scoped-cool",
        email="burn-scoped-cool@example.com",
    )
    await _insert_weekly_usage(account_id="anthropic-burn-scoped-hot", used_percent=30.0)
    await _insert_weekly_usage(account_id="anthropic-burn-scoped-cool", used_percent=62.0)
    await _insert_fable_scoped_weekly(account_id="anthropic-burn-scoped-hot", used_percent=100.0)
    await _insert_fable_scoped_weekly(account_id="anthropic-burn-scoped-cool", used_percent=10.0)

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-haiku-burn-scoped",
            "model": "claude-haiku-4-5",
            "quotaKey": "anthropic_standard",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-burn-scoped-hot"


@pytest.mark.asyncio
async def test_stale_fable_scoped_row_falls_back_to_overall_heuristic(async_client):
    await _insert_account(
        account_id="anthropic-scoped-stale",
        provider="anthropic",
        access_token="anthropic-access-scoped-stale",
        email="scoped-stale@example.com",
    )
    await _insert_weekly_usage(account_id="anthropic-scoped-stale", used_percent=10.0)
    stale_recorded_at = utcnow() - timedelta(hours=7)

    await _insert_fable_scoped_weekly(
        account_id="anthropic-scoped-stale",
        used_percent=100.0,
        recorded_at=stale_recorded_at,
    )

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-fable-scoped-stale",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-scoped-stale"
