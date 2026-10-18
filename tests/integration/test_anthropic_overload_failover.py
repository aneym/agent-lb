from __future__ import annotations

import pytest
from sqlalchemy import select

import app.modules.proxy.anthropic_service as anthropic_proxy_module
from app.db.models import AdditionalUsageHistory, RequestLog
from app.db.session import SessionLocal
from tests.integration.anthropic_proxy_support import (
    _OVERLOADED_529_BODY,
    ANTHROPIC_SSE_BYTES,
    _FakeResponse,
    _FakeResponseContext,
    _insert_account,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_upstream_529_fails_over_to_next_account(async_client, monkeypatch):

    await _insert_account(
        account_id="anthropic-brownout",
        provider="anthropic",
        access_token="anthropic-access-brownout",
        email="brownout@example.com",
    )
    await _insert_account(
        account_id="anthropic-healthy-529",
        provider="anthropic",
        access_token="anthropic-access-healthy",
        email="healthy-529@example.com",
    )

    upstream_tokens: list[str] = []

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, json_body
        token = headers["Authorization"]
        upstream_tokens.append(token)
        if len(upstream_tokens) == 1:
            return _FakeResponseContext(_FakeResponse(529, _OVERLOADED_529_BODY))
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_SSE_BYTES))

    backoff_sleeps: list[float] = []

    async def fake_backoff_sleep(delay: float) -> None:
        backoff_sleeps.append(delay)

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )
    monkeypatch.setattr(anthropic_proxy_module, "_OVERLOADED_RETRY_SLEEP", fake_backoff_sleep)
    monkeypatch.setattr(anthropic_proxy_module, "_OVERLOADED_RETRY_JITTER", lambda: 0.0)

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
    assert len(upstream_tokens) == 2
    assert upstream_tokens[0] != upstream_tokens[1]
    assert backoff_sleeps == [pytest.approx(0.25)]

    async with SessionLocal() as session:
        brownout_cooldowns = list(
            (
                await session.execute(
                    select(AdditionalUsageHistory).where(AdditionalUsageHistory.account_id == "anthropic-brownout")
                )
            ).scalars()
        )
        logs = list((await session.execute(select(RequestLog))).scalars())

    assert brownout_cooldowns == []
    error_logs = [log for log in logs if log.status == "error"]
    assert [log.error_code for log in error_logs] == ["upstream_529"]
    assert any(log.status == "success" for log in logs)


@pytest.mark.asyncio
async def test_upstream_529_full_outage_fails_fast_with_overloaded_error(async_client, monkeypatch):
    for index in range(4):
        await _insert_account(
            account_id=f"anthropic-outage-{index}",
            provider="anthropic",
            access_token=f"anthropic-access-outage-{index}",
            email=f"outage-{index}@example.com",
        )

    upstream_calls: list[str] = []

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, json_body
        upstream_calls.append(headers["Authorization"])
        return _FakeResponseContext(_FakeResponse(529, _OVERLOADED_529_BODY))

    backoff_sleeps: list[float] = []

    async def fake_backoff_sleep(delay: float) -> None:
        backoff_sleeps.append(delay)

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )
    monkeypatch.setattr(anthropic_proxy_module, "_OVERLOADED_RETRY_SLEEP", fake_backoff_sleep)
    monkeypatch.setattr(anthropic_proxy_module, "_OVERLOADED_RETRY_JITTER", lambda: 0.0)

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

    assert response.status_code == 529
    body = response.json()
    assert body["error"]["type"] == "overloaded_error"
    assert body["error"]["message"] == "Overloaded"

    assert len(upstream_calls) == 4
    assert len(set(upstream_calls)) == 4
    assert backoff_sleeps == [pytest.approx(0.25), pytest.approx(0.5), pytest.approx(1.0)]

    async with SessionLocal() as session:
        cooldowns = list((await session.execute(select(AdditionalUsageHistory))).scalars())
    assert cooldowns == []
