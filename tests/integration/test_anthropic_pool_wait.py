from __future__ import annotations

import json
import time
from datetime import timedelta, timezone

import pytest
from sqlalchemy import select

import app.modules.proxy.anthropic_service as anthropic_proxy_module
from app.core.utils.time import utcnow
from app.db.models import AdditionalUsageHistory
from app.db.session import SessionLocal
from tests.integration.anthropic_proxy_support import (
    ANTHROPIC_SSE_BYTES,
    _FakeResponse,
    _FakeResponseContext,
    _insert_account,
    _insert_quota_cooldown,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_pool_exhausted_streaming_request_waits_for_reset_and_serves(async_client, monkeypatch):

    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_POOL_EXHAUSTED_WAIT_ENABLED", "true")
    get_settings.cache_clear()
    await _insert_account(
        account_id="anthropic-waiting",
        provider="anthropic",
        access_token="anthropic-access-waiting",
        email="waiting@example.com",
    )
    reset_at = int((utcnow() + timedelta(minutes=5)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_quota_cooldown(
        account_id="anthropic-waiting",
        quota_key="anthropic_top_thinking",
        reset_at=reset_at,
    )

    fake_now = {"value": 1_000_000.0}
    sleep_calls: list[float] = []

    def fake_clock() -> float:
        return fake_now["value"]

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)
        fake_now["value"] += delay

        async with SessionLocal() as session:
            result = await session.execute(
                select(AdditionalUsageHistory).where(AdditionalUsageHistory.account_id == "anthropic-waiting")
            )
            for row in result.scalars():
                row.reset_at = int((utcnow() - timedelta(seconds=1)).replace(tzinfo=timezone.utc).timestamp())
            await session.commit()

    monkeypatch.setattr(anthropic_proxy_module, "_POOL_WAIT_CLOCK", fake_clock)
    monkeypatch.setattr(anthropic_proxy_module, "_POOL_WAIT_SLEEP", fake_sleep)
    monkeypatch.setattr(anthropic_proxy_module, "_POOL_WAIT_JITTER", lambda: 0.0)

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers, json_body
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_SSE_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    async with async_client.stream(
        "POST",
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    ) as response:
        assert response.status_code == 200
        body = await response.aread()

    assert sleep_calls, "the stream must hold before serving"
    text = body.decode("utf-8")
    assert "event: message_stop" in text
    assert "event: error" not in text


@pytest.mark.asyncio
async def test_pool_exhausted_wait_cap_expiry_emits_structured_rate_limit_error(async_client, monkeypatch):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_POOL_EXHAUSTED_WAIT_ENABLED", "true")
    monkeypatch.setenv("AGENT_LB_ANTHROPIC_POOL_EXHAUSTED_WAIT_MAX_SECONDS", "400")
    get_settings.cache_clear()

    await _insert_account(
        account_id="anthropic-capped",
        provider="anthropic",
        access_token="anthropic-access-capped",
        email="capped@example.com",
    )
    reset_at = int((utcnow() + timedelta(hours=3)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_quota_cooldown(
        account_id="anthropic-capped",
        quota_key="anthropic_top_thinking",
        reset_at=reset_at,
    )

    fake_now = {"value": 2_000_000.0}
    sleep_calls: list[float] = []

    def fake_clock() -> float:
        return fake_now["value"]

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)
        fake_now["value"] += delay

    monkeypatch.setattr(anthropic_proxy_module, "_POOL_WAIT_CLOCK", fake_clock)
    monkeypatch.setattr(anthropic_proxy_module, "_POOL_WAIT_SLEEP", fake_sleep)
    monkeypatch.setattr(anthropic_proxy_module, "_POOL_WAIT_JITTER", lambda: 0.0)

    async with async_client.stream(
        "POST",
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    ) as response:
        assert response.status_code == 200
        body = await response.aread()

    assert sleep_calls, "the stream must hold before capping out"
    text = body.decode("utf-8")
    assert "event: error\n" in text
    error_data = json.loads(text.split("event: error\ndata: ", 1)[1].split("\n\n", 1)[0])
    assert error_data["error"]["type"] == "rate_limit_error"


@pytest.mark.asyncio
async def test_pool_exhausted_wait_defaults_to_immediate_envelope(async_client, monkeypatch):
    from app.core.config.settings import get_settings

    monkeypatch.delenv("AGENT_LB_ANTHROPIC_POOL_EXHAUSTED_WAIT_ENABLED", raising=False)
    get_settings.cache_clear()
    await _insert_account(
        account_id="anthropic-no-wait",
        provider="anthropic",
        access_token="anthropic-access-no-wait",
        email="no-wait@example.com",
    )
    reset_at = int((utcnow() + timedelta(minutes=7)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_quota_cooldown(
        account_id="anthropic-no-wait",
        quota_key="anthropic_top_thinking",
        reset_at=reset_at,
    )

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 429
    body = response.json()
    assert body["error"]["type"] == "rate_limit_error"

    assert 0 < int(response.headers["anthropic-ratelimit-unified-reset"]) - time.time() <= 60
