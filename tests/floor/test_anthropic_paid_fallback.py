from __future__ import annotations

from datetime import timedelta, timezone

import pytest

import app.modules.proxy.anthropic_service as anthropic_proxy_module
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.dependencies import _proxy_repo_context
from tests.floor.anthropic_proxy_support import (
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
async def test_fable_extra_usage_tripwire_excludes_paid_rows_while_headroom_routes(
    async_client,
    monkeypatch,
):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE", "true")
    get_settings.cache_clear()

    headroom_ids = [f"anthropic-fable-scoped-exhausted-{index}" for index in range(3)]
    hard_exclusion_reset_at = int((utcnow() + timedelta(days=2)).replace(tzinfo=timezone.utc).timestamp())
    for account_id in headroom_ids:
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
            reset_at=hard_exclusion_reset_at,
        )

    await _insert_account(
        account_id="anthropic-fable-paid",
        provider="anthropic",
        access_token="anthropic-access-fable-paid",
        email="fable-paid@example.com",
        status=AccountStatus.RATE_LIMITED,
    )
    await _insert_account(
        account_id="anthropic-planner-messages-no-credits",
        provider="anthropic",
        access_token="anthropic-access-planner-messages-no-credits",
        email="fable-exhausted-no-credits@example.com",
    )
    paid_reset_at = int((utcnow() + timedelta(hours=1)).replace(tzinfo=timezone.utc).timestamp())
    no_credit_reset_at = paid_reset_at + 1800
    await _insert_primary_usage(
        account_id="anthropic-fable-paid",
        used_percent=100.0,
        reset_at=paid_reset_at,
        credits_has=True,
        credits_balance=250.0,
    )
    await _insert_primary_usage(
        account_id="anthropic-planner-messages-no-credits",
        used_percent=100.0,
        reset_at=no_credit_reset_at,
        credits_has=False,
        credits_balance=0.0,
    )
    for account_id, reset_at in (
        ("anthropic-fable-paid", paid_reset_at),
        ("anthropic-planner-messages-no-credits", no_credit_reset_at),
    ):
        await _insert_weekly_usage(account_id=account_id, used_percent=20.0)
        await _insert_fable_scoped_weekly(account_id=account_id, used_percent=20.0)
        await _insert_quota_cooldown(
            account_id=account_id,
            quota_key=anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
            reset_at=reset_at,
        )

    eligibility = await anthropic_proxy_module.AnthropicProxyService(_proxy_repo_context)._provider_quota_eligibility(
        "anthropic",
        "anthropic_top_thinking",
        model="claude-fable-5",
    )

    assert eligibility.account_ids == headroom_ids

    assert eligibility.paid_fallback_account_ids == frozenset()
    assert eligibility.blocked_count == 2
    assert eligibility.next_reset_at == paid_reset_at

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-live-shaped-fable-paid-fallback",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] in headroom_ids
    async with SessionLocal() as session:
        paid_account = await session.get(Account, "anthropic-fable-paid")
        assert paid_account is not None
        assert paid_account.status == AccountStatus.RATE_LIMITED


@pytest.mark.asyncio
async def test_fable_soft_headroom_blocks_paid_fallback(async_client, monkeypatch):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE", "true")
    get_settings.cache_clear()

    await _insert_account(
        account_id="anthropic-fable-paid-preferred",
        provider="anthropic",
        access_token="anthropic-access-fable-paid-preferred",
        email="fable-paid-preferred@example.com",
        status=AccountStatus.RATE_LIMITED,
    )
    await _insert_account(
        account_id="anthropic-fable-soft-headroom",
        provider="anthropic",
        access_token="anthropic-access-fable-soft-headroom",
        email="fable-soft-headroom@example.com",
    )
    reset_at = int((utcnow() + timedelta(hours=1)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_primary_usage(
        account_id="anthropic-fable-paid-preferred",
        used_percent=100.0,
        reset_at=reset_at,
        credits_has=True,
        credits_balance=250.0,
    )
    await _insert_weekly_usage(account_id="anthropic-fable-paid-preferred", used_percent=20.0)
    await _insert_fable_scoped_weekly(account_id="anthropic-fable-paid-preferred", used_percent=20.0)
    await _insert_quota_cooldown(
        account_id="anthropic-fable-paid-preferred",
        quota_key=anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
        reset_at=reset_at,
    )
    await _insert_primary_usage(account_id="anthropic-fable-soft-headroom", used_percent=10.0)
    await _insert_weekly_usage(account_id="anthropic-fable-soft-headroom", used_percent=80.0)

    eligibility = await anthropic_proxy_module.AnthropicProxyService(_proxy_repo_context)._provider_quota_eligibility(
        "anthropic",
        "anthropic_top_thinking",
        model="claude-fable-5",
    )
    assert eligibility.account_ids == ["anthropic-fable-soft-headroom"]
    assert eligibility.paid_fallback_account_ids == frozenset()

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-fable-soft-headroom-before-paid",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-fable-soft-headroom"


@pytest.mark.asyncio
async def test_route_to_extra_usage_paid_only_eligibility_has_no_blocked_reset(async_client, monkeypatch):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE", "true")
    get_settings.cache_clear()

    await _insert_account(
        account_id="anthropic-paid-only",
        provider="anthropic",
        access_token="anthropic-access-paid-only",
        email="paid-only@example.com",
    )
    reset_at = int((utcnow() + timedelta(hours=1)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_primary_usage(
        account_id="anthropic-paid-only",
        used_percent=100.0,
        reset_at=reset_at,
        credits_has=True,
        credits_balance=25.0,
    )
    await _insert_quota_cooldown(
        account_id="anthropic-paid-only",
        quota_key=anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
        reset_at=reset_at,
    )

    eligibility = await anthropic_proxy_module.AnthropicProxyService(_proxy_repo_context)._provider_quota_eligibility(
        "anthropic",
        "anthropic_top_thinking",
        model="claude-fable-5",
    )

    assert eligibility.account_ids == ["anthropic-paid-only"]
    assert eligibility.blocked_count == 0
    assert eligibility.next_reset_at is None
    assert eligibility.paid_fallback_account_ids == frozenset({"anthropic-paid-only"})


@pytest.mark.parametrize("credits_balance", [None, 0.0])
@pytest.mark.asyncio
async def test_route_to_extra_usage_requires_positive_finite_credit_balance(
    async_client,
    monkeypatch,
    credits_balance,
):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE", "true")
    get_settings.cache_clear()

    await _insert_account(
        account_id="anthropic-exhausted-no-credits",
        provider="anthropic",
        access_token="anthropic-access-no-credits",
        email="no-credits@example.com",
    )
    reset_at = int((utcnow() + timedelta(hours=2)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_primary_usage(
        account_id="anthropic-exhausted-no-credits",
        used_percent=100.0,
        reset_at=reset_at,
        credits_has=True,
        credits_balance=credits_balance,
    )
    await _insert_quota_cooldown(
        account_id="anthropic-exhausted-no-credits",
        quota_key=anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
        reset_at=reset_at,
    )

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-no-extra-usage-credits",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "anthropic_quota_cooldown"


@pytest.mark.asyncio
async def test_route_to_extra_usage_does_not_bypass_real_request_quota_cooldown(async_client, monkeypatch):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE", "true")
    get_settings.cache_clear()

    await _insert_account(
        account_id="anthropic-exhausted-with-credits",
        provider="anthropic",
        access_token="anthropic-access-with-credits",
        email="with-credits@example.com",
    )
    reset_at = int((utcnow() + timedelta(hours=2)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_primary_usage(
        account_id="anthropic-exhausted-with-credits",
        used_percent=100.0,
        reset_at=reset_at,
        credits_has=True,
        credits_balance=25.0,
    )
    await _insert_quota_cooldown(
        account_id="anthropic-exhausted-with-credits",
        quota_key="anthropic_top_thinking",
        reset_at=reset_at,
    )

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-model-cooldown",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "anthropic_quota_cooldown"


@pytest.mark.asyncio
async def test_route_to_extra_usage_does_not_bypass_secondary_exhaustion(async_client, monkeypatch):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE", "true")
    get_settings.cache_clear()

    await _insert_account(
        account_id="anthropic-primary-and-weekly-exhausted",
        provider="anthropic",
        access_token="anthropic-access-primary-and-weekly-exhausted",
        email="primary-and-weekly-exhausted@example.com",
    )
    primary_reset_at = int((utcnow() + timedelta(hours=2)).replace(tzinfo=timezone.utc).timestamp())
    secondary_reset_at = int((utcnow() + timedelta(days=5)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_primary_usage(
        account_id="anthropic-primary-and-weekly-exhausted",
        used_percent=100.0,
        reset_at=primary_reset_at,
        credits_has=True,
        credits_balance=25.0,
    )
    await _insert_weekly_usage(
        account_id="anthropic-primary-and-weekly-exhausted",
        used_percent=100.0,
        reset_at=secondary_reset_at,
    )
    await _insert_quota_cooldown(
        account_id="anthropic-primary-and-weekly-exhausted",
        quota_key=anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
        reset_at=primary_reset_at,
    )

    eligibility = await anthropic_proxy_module.AnthropicProxyService(_proxy_repo_context)._provider_quota_eligibility(
        "anthropic",
        "anthropic_top_thinking",
        model="claude-fable-5",
    )
    assert eligibility.account_ids == []
    assert eligibility.paid_fallback_account_ids == frozenset()

    assert eligibility.next_reset_at == primary_reset_at

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

    assert response.status_code == 429
    assert response.headers["anthropic-ratelimit-unified-reset"] == str(primary_reset_at)
    assert upstream_calls == []


@pytest.mark.asyncio
async def test_count_tokens_stays_exempt_from_message_quota_cooldowns(async_client, monkeypatch):

    await _insert_account(
        account_id="anthropic-exhausted-count",
        provider="anthropic",
        access_token="anthropic-access-count",
        email="count@example.com",
    )
    await _insert_primary_usage(account_id="anthropic-exhausted-count", used_percent=99.0)
    cooldown_reset = int((utcnow() + timedelta(hours=1)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_quota_cooldown(
        account_id="anthropic-exhausted-count",
        quota_key="anthropic_top_thinking",
        reset_at=cooldown_reset,
    )

    def fake_open_count_tokens_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers, json_body
        return _FakeResponseContext(
            _FakeResponse(200, b'{"input_tokens": 12}', headers={"content-type": "application/json"})
        )

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_count_tokens_response",
        fake_open_count_tokens_response,
    )

    response = await async_client.post(
        "/v1/messages/count_tokens",
        json={
            "model": "claude-fable-5",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 200
    assert response.json() == {"input_tokens": 12}
