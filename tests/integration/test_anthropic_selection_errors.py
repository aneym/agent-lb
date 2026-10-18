from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import aiohttp
import pytest

import app.modules.proxy.anthropic_service as anthropic_proxy_module
from app.core.utils.time import utcnow
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.anthropic_proxy_support import (
    ANTHROPIC_SSE_BYTES,
    _FailingConnectContext,
    _FakeResponse,
    _FakeResponseContext,
    _insert_account,
    _insert_fable_scoped_weekly,
    _insert_quota_cooldown,
    _insert_weekly_usage,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_anthropic_selection_failure_separates_cooldowns_from_exhausted_windows(async_client, monkeypatch):

    await _insert_account(
        account_id="anthropic-cooling",
        provider="anthropic",
        access_token="anthropic-access-cooling",
        email="cooling@example.com",
    )
    cooldown_recorded_at = utcnow().replace(microsecond=0)
    cooldown_reset_at = int((cooldown_recorded_at + timedelta(minutes=6)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_quota_cooldown(
        account_id="anthropic-cooling",
        quota_key="anthropic_opus",
        reset_at=cooldown_reset_at,
        recorded_at=cooldown_recorded_at,
    )

    bounded_retry_at = int(cooldown_recorded_at.replace(tzinfo=timezone.utc).timestamp()) + 60
    await _insert_account(
        account_id="anthropic-weekly-spent",
        provider="anthropic",
        access_token="anthropic-access-weekly",
        email="weekly@example.com",
    )
    weekly_reset_at = int((utcnow() + timedelta(days=3)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_weekly_usage(
        account_id="anthropic-weekly-spent",
        used_percent=100.0,
        reset_at=weekly_reset_at,
    )

    await _insert_account(
        account_id="anthropic-healthy",
        provider="anthropic",
        access_token="anthropic-access-healthy",
        email="healthy@example.com",
    )

    async def fake_select_account(self, **kwargs):
        del self, kwargs
        return AccountSelection(account=None, error_message="No available accounts", error_code=None)

    monkeypatch.setattr(anthropic_proxy_module.LoadBalancer, "select_account", fake_select_account)

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-route-cooldown-split",
            "model": "claude-opus-5",
            "quotaKey": "anthropic_top",
        },
    )

    assert response.status_code == 503
    message = response.json()["error"]["message"]
    quota_sentence = message.split("Model quota: ", 1)[1].split(". ", 1)[0]
    clauses = [clause.strip() for clause in quota_sentence.split(";")]

    assert clauses == [
        f"anthropic_opus cooldown excluded 1 account until {datetime.fromtimestamp(bounded_retry_at).isoformat()}",
        "1 account out of window or capped",
        "1 account remained after the anthropic_opus prefilter",
    ]
    assert 0 < bounded_retry_at - time.time() <= 60
    assert datetime.fromtimestamp(weekly_reset_at).isoformat() not in message


@pytest.mark.asyncio
async def test_opus_routes_when_fable_weekly_and_legacy_cooldown_are_exhausted(async_client):
    account_id = "opus-not-fable"
    await _insert_account(
        account_id=account_id,
        provider="anthropic",
        access_token="test-opus",
        email="opus@example.invalid",
    )
    reset_at = int((utcnow() + timedelta(days=2)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_quota_cooldown(account_id=account_id, quota_key="anthropic_top_thinking", reset_at=reset_at)
    await _insert_fable_scoped_weekly(account_id=account_id, used_percent=100.0, reset_at=reset_at)
    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "opus-independent",
            "model": "claude-opus-5-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["accountId"] == account_id
    blocked = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "fable-blocked",
            "model": "claude-fable-5-1",
            "quotaKey": "anthropic_top_thinking",
        },
    )
    assert blocked.status_code == 429


@pytest.mark.asyncio
async def test_opus_specific_cooldown_remains_a_real_blocker(async_client):
    account_id = "opus-cooling"
    await _insert_account(
        account_id=account_id,
        provider="anthropic",
        access_token="test-opus",
        email="opus@example.invalid",
    )
    reset_at = int((utcnow() + timedelta(hours=2)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_quota_cooldown(account_id=account_id, quota_key="anthropic_opus_thinking", reset_at=reset_at)
    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "opus-real-limit",
            "model": "claude-opus-5-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )
    assert response.status_code == 429
    assert response.json()["error"]["retryAt"].endswith("Z")


@pytest.mark.asyncio
async def test_upstream_connect_timeout_retries_then_succeeds(async_client, monkeypatch):
    await _insert_account(
        account_id="anthropic-connect-retry",
        provider="anthropic",
        access_token="anthropic-access-connect-retry",
        email="connect-retry@example.com",
    )

    opens: list[int] = []

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers, json_body
        opens.append(len(opens))
        if len(opens) <= 2:
            return _FailingConnectContext(
                aiohttp.ServerTimeoutError("Connection timeout to host https://api.anthropic.com/v1/messages")
            )
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_SSE_BYTES))

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )
    monkeypatch.setattr(anthropic_proxy_module, "_CONNECT_RETRY_SLEEP", fake_sleep)

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 200
    assert len(opens) == 3
    assert sleeps == [pytest.approx(0.5), pytest.approx(1.0)]


@pytest.mark.asyncio
async def test_upstream_connect_failure_exhausted_returns_503_not_500(async_client, monkeypatch):
    await _insert_account(
        account_id="anthropic-connect-dead",
        provider="anthropic",
        access_token="anthropic-access-connect-dead",
        email="connect-dead@example.com",
    )

    opens: list[int] = []

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers, json_body
        opens.append(len(opens))
        return _FailingConnectContext(
            aiohttp.ServerTimeoutError("Connection timeout to host https://api.anthropic.com/v1/messages")
        )

    async def fake_sleep(delay: float) -> None:
        del delay

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )
    monkeypatch.setattr(anthropic_proxy_module, "_CONNECT_RETRY_SLEEP", fake_sleep)

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 503
    assert "upstream_unreachable" in response.text
    assert len(opens) == 3
