from __future__ import annotations

from datetime import timedelta, timezone
from typing import Any

import pytest

import app.modules.proxy.anthropic_service as anthropic_proxy_module
from app.core.utils.time import utcnow
from app.dependencies import _proxy_repo_context
from tests.integration.anthropic_proxy_support import (
    ANTHROPIC_SSE_BYTES,
    _FakeResponse,
    _FakeResponseContext,
    _insert_account,
    _insert_primary_usage,
    _insert_quota_cooldown,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_session_route_admits_recovered_primary_despite_historical_tripwire(async_client):

    await _insert_account(
        account_id="anthropic-tripwire-recovered",
        provider="anthropic",
        access_token="anthropic-access-tripwire-recovered",
        email="tripwire-recovered@example.com",
    )
    future_reset = int((utcnow() + timedelta(hours=2)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_quota_cooldown(
        account_id="anthropic-tripwire-recovered",
        quota_key=anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
        reset_at=future_reset,
        recorded_at=utcnow() - timedelta(minutes=5),
    )
    await _insert_primary_usage(
        account_id="anthropic-tripwire-recovered",
        used_percent=0.0,
        reset_at=future_reset,
    )

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-tripwire-recovered",
            "model": "claude-sonnet-4-6",
            "quotaKey": "anthropic_top",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-tripwire-recovered"


@pytest.mark.asyncio
async def test_exhausted_primary_window_account_is_not_selected_when_alternative_exists(async_client, monkeypatch):

    await _insert_account(
        account_id="anthropic-exhausted",
        provider="anthropic",
        access_token="anthropic-access-exhausted",
        email="exhausted@example.com",
    )
    await _insert_account(
        account_id="anthropic-headroom",
        provider="anthropic",
        access_token="anthropic-access-headroom",
        email="headroom@example.com",
    )
    exhausted_reset = int((utcnow() + timedelta(hours=2)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_primary_usage(
        account_id="anthropic-exhausted",
        used_percent=100.0,
        reset_at=exhausted_reset,
        credits_has=True,
    )
    await _insert_quota_cooldown(
        account_id="anthropic-exhausted",
        quota_key=anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
        reset_at=exhausted_reset,
    )
    await _insert_primary_usage(account_id="anthropic-headroom", used_percent=10.0)

    captured: dict[str, Any] = {}

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, json_body
        captured["authorization"] = headers["Authorization"]
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
    assert captured["authorization"] == "Bearer anthropic-access-headroom"


@pytest.mark.asyncio
async def test_pool_of_exhausted_window_snapshots_still_attempts_upstream_by_default(async_client, monkeypatch):

    await _insert_account(
        account_id="anthropic-exhausted-a",
        provider="anthropic",
        access_token="anthropic-access-a",
        email="a@example.com",
    )
    await _insert_account(
        account_id="anthropic-exhausted-b",
        provider="anthropic",
        access_token="anthropic-access-b",
        email="b@example.com",
    )
    earliest_reset = int((utcnow() + timedelta(hours=1)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_primary_usage(
        account_id="anthropic-exhausted-a",
        used_percent=100.0,
        reset_at=earliest_reset,
        credits_has=True,
        credits_balance=50.0,
    )
    await _insert_quota_cooldown(
        account_id="anthropic-exhausted-a",
        quota_key=anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
        reset_at=earliest_reset,
    )
    await _insert_primary_usage(
        account_id="anthropic-exhausted-b",
        used_percent=100.0,
        reset_at=earliest_reset + 3600,
    )

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
@pytest.mark.parametrize(
    ("credits_has", "credits_unlimited", "credits_balance"),
    [(True, False, 186.6), (False, True, None)],
)
async def test_route_to_extra_usage_opt_in_serves_as_last_resort(
    async_client,
    monkeypatch,
    credits_has,
    credits_unlimited,
    credits_balance,
):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE", "true")
    get_settings.cache_clear()

    await _insert_account(
        account_id="anthropic-exhausted-only",
        provider="anthropic",
        access_token="anthropic-access-exhausted",
        email="exhausted@example.com",
    )
    await _insert_account(
        account_id="anthropic-exhausted-without-credits",
        provider="anthropic",
        access_token="anthropic-access-without-credits",
        email="without-credits@example.com",
    )
    paid_reset_at = int((utcnow() + timedelta(hours=1)).replace(tzinfo=timezone.utc).timestamp())
    blocked_reset_at = paid_reset_at + 3600
    await _insert_primary_usage(
        account_id="anthropic-exhausted-only",
        used_percent=100.0,
        reset_at=paid_reset_at,
        credits_has=credits_has,
        credits_unlimited=credits_unlimited,
        credits_balance=credits_balance,
    )
    await _insert_primary_usage(
        account_id="anthropic-exhausted-without-credits",
        used_percent=100.0,
        reset_at=blocked_reset_at,
        credits_has=False,
        credits_balance=0.0,
    )
    await _insert_quota_cooldown(
        account_id="anthropic-exhausted-only",
        quota_key=anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
        reset_at=paid_reset_at,
    )
    await _insert_quota_cooldown(
        account_id="anthropic-exhausted-without-credits",
        quota_key=anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
        reset_at=blocked_reset_at,
    )

    eligibility = await anthropic_proxy_module.AnthropicProxyService(_proxy_repo_context)._provider_quota_eligibility(
        "anthropic",
        "anthropic_top_thinking",
        model="claude-fable-5",
    )
    assert eligibility.account_ids == ["anthropic-exhausted-only"]
    assert eligibility.blocked_count == 1
    assert eligibility.next_reset_at == blocked_reset_at
    assert eligibility.paid_fallback_account_ids == frozenset({"anthropic-exhausted-only"})

    upstream_calls: list[str] = []

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, json_body
        upstream_calls.append(headers["Authorization"])
        return _FakeResponseContext(
            _FakeResponse(
                200,
                ANTHROPIC_SSE_BYTES,
                headers={
                    "anthropic-ratelimit-unified-overage-in-use": "true",
                    "anthropic-ratelimit-unified-reset": str(paid_reset_at),
                },
            )
        )

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
    assert response.content == ANTHROPIC_SSE_BYTES
    assert upstream_calls == ["Bearer anthropic-access-exhausted"]
