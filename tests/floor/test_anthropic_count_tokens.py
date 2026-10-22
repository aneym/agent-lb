from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

import app.modules.proxy.anthropic_service as anthropic_proxy_module
from app.db.models import Account, AccountStatus, AdditionalUsageHistory
from app.db.request_models import RequestLog
from app.db.session import SessionLocal
from tests.floor.anthropic_proxy_support import ANTHROPIC_JSON_BYTES, ANTHROPIC_SSE_BYTES, _FakeResponse, _FakeResponseContext, _insert_account  # noqa: I001  # fmt: skip

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_kimi_count_tokens_served_locally_without_upstream_call(async_client, monkeypatch):

    await _insert_account(
        account_id="kimi-account",
        provider="kimi",
        access_token="kimi-access",
        email="kimi@example.com",
    )

    def fail_open_count_tokens_response(self, session, *, provider_name, headers, json_body):
        raise AssertionError(f"count_tokens must not reach upstream for {provider_name}")

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_count_tokens_response",
        fail_open_count_tokens_response,
    )

    response = await async_client.post(
        "/v1/messages/count_tokens",
        json={"model": "kimi-k3", "messages": [{"role": "user", "content": "hello"}]},
        headers={"authorization": "Bearer client-token"},
    )

    assert response.status_code == 200
    assert response.json()["input_tokens"] > 0


@pytest.mark.asyncio
async def test_anthropic_count_tokens_forwards_verbatim_without_usage_writes(async_client, monkeypatch):
    await _insert_account(
        account_id="anthropic-account",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )

    captured: dict[str, Any] = {}

    def fake_open_count_tokens_response(self, session, *, provider_name, headers, json_body):
        del self, session
        assert provider_name == "anthropic"
        captured["headers"] = dict(headers)
        captured["json_body"] = dict(json_body)
        return _FakeResponseContext(
            _FakeResponse(200, b'{"input_tokens": 2095}', headers={"content-type": "application/json"})
        )

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_count_tokens_response",
        fake_open_count_tokens_response,
    )

    payload = {
        "model": "claude-fable-5",
        "system": "Claude Code system prompt prefix must pass through untouched.",
        "messages": [{"role": "user", "content": "hello"}],
        "thinking": {"type": "adaptive"},
    }
    response = await async_client.post(
        "/v1/messages/count_tokens",
        json=payload,
        headers={
            "anthropic-beta": "oauth-2025-04-20",
            "authorization": "Bearer client-token",
            "x-api-key": "client-placeholder",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"input_tokens": 2095}
    assert captured["json_body"] == payload
    assert captured["headers"]["Authorization"] == "Bearer anthropic-access"
    assert captured["headers"]["anthropic-beta"] == "oauth-2025-04-20"
    assert "x-api-key" not in {key.lower() for key in captured["headers"]}

    async with SessionLocal() as session:
        logs = list((await session.execute(select(RequestLog))).scalars())
        cooldowns = list((await session.execute(select(AdditionalUsageHistory))).scalars())

    assert logs == []
    assert cooldowns == []


@pytest.mark.asyncio
async def test_anthropic_count_tokens_passes_through_upstream_error_envelope(async_client, monkeypatch):
    await _insert_account(
        account_id="anthropic-account",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )

    upstream_error = b'{"type":"error","error":{"type":"rate_limit_error","message":"slow down"}}'

    def fake_open_count_tokens_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers, json_body
        return _FakeResponseContext(_FakeResponse(429, upstream_error, headers={"content-type": "application/json"}))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_count_tokens_response",
        fake_open_count_tokens_response,
    )

    response = await async_client.post(
        "/v1/messages/count_tokens",
        json={"model": "claude-fable-5", "messages": [{"role": "user", "content": "hello"}]},
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 429
    assert response.content == upstream_error

    async with SessionLocal() as session:
        account = (await session.execute(select(Account))).scalar_one()
        cooldowns = list((await session.execute(select(AdditionalUsageHistory))).scalars())

    assert account.status == AccountStatus.ACTIVE
    assert cooldowns == []


@pytest.mark.asyncio
async def test_anthropic_fast_mode_adds_required_oauth_and_fast_betas(async_client, monkeypatch):
    await _insert_account(
        account_id="anthropic-account",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )

    captured: dict[str, Any] = {}

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, json_body
        assert provider_name == "anthropic"
        captured["headers"] = dict(headers)
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_SSE_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    payload = {
        "model": "claude-opus-4-8",
        "max_tokens": 32,
        "stream": True,
        "messages": [{"role": "user", "content": "hello"}],
        "speed": "fast",
    }
    async with async_client.stream("POST", "/v1/messages", json=payload, headers={}) as response:
        assert response.status_code == 200
        await response.aread()

    betas = {value.strip() for value in captured["headers"]["anthropic-beta"].split(",")}
    assert {"oauth-2025-04-20", "fast-mode-2026-02-01"}.issubset(betas)
    assert captured["headers"]["Authorization"] == "Bearer anthropic-access"


@pytest.mark.asyncio
async def test_glm_messages_selects_glm_account_and_upstream(async_client, monkeypatch):
    await _insert_account(
        account_id="anthropic-account",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )
    await _insert_account(
        account_id="glm-account",
        provider="glm",
        access_token="glm-access",
        email="glm@example.com",
    )

    captured: dict[str, Any] = {}

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session
        captured["provider_name"] = provider_name
        captured["headers"] = dict(headers)
        captured["json_body"] = dict(json_body)
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_JSON_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "glm-5.2",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 200
    assert captured["provider_name"] == "glm"
    assert captured["headers"]["Authorization"] == "Bearer glm-access"
    assert captured["json_body"]["model"] == "glm-5.2"

    async with SessionLocal() as session:
        log = (await session.execute(select(RequestLog))).scalar_one()

    assert log.provider == "glm"
    assert log.account_id == "glm-account"
    assert log.status == "success"
