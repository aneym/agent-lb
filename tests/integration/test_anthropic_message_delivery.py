from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

import app.modules.proxy.anthropic_service as anthropic_proxy_module
from app.core.anthropic.models import AnthropicMessageRequest
from app.db.models import Account, RequestLog, StickySession, StickySessionKind
from app.db.session import SessionLocal
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.anthropic_proxy_support import (
    ANTHROPIC_JSON_BYTES,
    _FakeResponse,
    _FakeResponseContext,
    _insert_account,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["k3-256k", "kimi-k3"])
async def test_kimi_messages_selects_kimi_account_and_upstream(async_client, monkeypatch, model):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_KIMI_ANTHROPIC_UPSTREAM_BASE_URL", "https://api.kimi.com/coding")
    get_settings.cache_clear()

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
    await _insert_account(
        account_id="kimi-account",
        provider="kimi",
        access_token="kimi-access",
        email="kimi@example.com",
    )

    captured: dict[str, Any] = {}

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session
        captured["provider_name"] = provider_name
        captured["base_url"] = anthropic_proxy_module._upstream_base_url(provider_name)
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
            "model": model,
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 200
    assert captured["provider_name"] == "kimi"
    assert captured["base_url"] == "https://api.kimi.com/coding"
    assert captured["headers"]["Authorization"] == "Bearer kimi-access"
    assert captured["json_body"]["model"] == model

    async with SessionLocal() as session:
        log = (await session.execute(select(RequestLog))).scalar_one()

    assert log.provider == "kimi"
    assert log.account_id == "kimi-account"
    assert log.status == "success"


@pytest.mark.asyncio
async def test_anthropic_messages_non_streaming_logs_usage(async_client, monkeypatch):
    await _insert_account(
        account_id="anthropic-account",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers, json_body
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_JSON_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"anthropic-beta": "oauth-2025-04-20", "x-api-key": "client-placeholder"},
    )
    assert response.status_code == 200
    assert response.content == ANTHROPIC_JSON_BYTES

    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog))
        logs = list(result.scalars())

    assert len(logs) == 1
    log = logs[0]
    assert log.provider == "anthropic"
    assert log.status == "success"
    assert log.input_tokens == 12
    assert log.output_tokens == 7
    assert log.cache_creation_tokens == 2
    assert log.cache_read_tokens == 1
    assert log.cost_usd is not None
    assert log.latency_first_token_ms is None


@pytest.mark.asyncio
async def test_anthropic_messages_non_streaming_upstream_529_returns_native_error(async_client, monkeypatch):
    await _insert_account(
        account_id="anthropic-overloaded",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers, json_body
        return _FakeResponseContext(
            _FakeResponse(
                529,
                b'{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}',
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
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 529
    assert response.json() == {
        "type": "error",
        "error": {"type": "overloaded_error", "message": "Overloaded"},
    }

    async with SessionLocal() as session:
        log = (await session.execute(select(RequestLog))).scalar_one()

    assert log.status == "error"
    assert log.error_code == "upstream_529"
    assert log.error_message == "Overloaded"


@pytest.mark.asyncio
async def test_anthropic_messages_passes_primary_reset_preference_to_selector(async_client, monkeypatch):
    await _insert_account(
        account_id="anthropic-primary-reset",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )

    class _SettingsCache:
        async def get(self) -> SimpleNamespace:
            return SimpleNamespace(prefer_earlier_reset_accounts=True)

    seen_selection_kwargs: list[dict[str, Any]] = []

    async def fake_select_account(self, **kwargs):
        del self
        seen_selection_kwargs.append(kwargs)
        async with SessionLocal() as session:
            account = await session.get(Account, "anthropic-primary-reset")
        return AccountSelection(account=account, error_message=None)

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers, json_body
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_JSON_BYTES))

    monkeypatch.setattr(anthropic_proxy_module, "get_settings_cache", lambda: _SettingsCache())
    monkeypatch.setattr(anthropic_proxy_module.LoadBalancer, "select_account", fake_select_account)
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
    assert seen_selection_kwargs[0]["prefer_earlier_reset_accounts"] is True
    assert seen_selection_kwargs[0]["prefer_earlier_reset_window"] == "primary"
    assert seen_selection_kwargs[0]["routing_strategy"] == "usage_weighted"


@pytest.mark.asyncio
async def test_anthropic_messages_keep_same_session_on_sticky_account(async_client, monkeypatch):
    await _insert_account(
        account_id="anthropic-a",
        provider="anthropic",
        access_token="anthropic-access-a",
        email="claude-a@example.com",
    )
    await _insert_account(
        account_id="anthropic-b",
        provider="anthropic",
        access_token="anthropic-access-b",
        email="claude-b@example.com",
    )

    seen_authorizations: list[str] = []

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, json_body
        assert provider_name == "anthropic"
        seen_authorizations.append(headers["Authorization"])
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_JSON_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    payload = {
        "model": "claude-fable-5",
        "max_tokens": 32,
        "messages": [{"role": "user", "content": "keep me sticky"}],
        "thinking": {"type": "adaptive"},
    }
    for _ in range(2):
        response = await async_client.post(
            "/v1/messages",
            json=payload,
            headers={
                "anthropic-beta": "oauth-2025-04-20",
                "x-claude-session-id": "claude-session-sticky",
            },
        )
        assert response.status_code == 200

    assert len(seen_authorizations) == 2
    assert seen_authorizations[1] == seen_authorizations[0]

    request = AnthropicMessageRequest.model_validate(payload)
    sticky_key = anthropic_proxy_module._anthropic_sticky_key(
        request,
        {"x-claude-session-id": "claude-session-sticky"},
        quota_key=anthropic_proxy_module._messages_affinity_quota_key(request, provider_name="anthropic"),
    )
    async with SessionLocal() as session:
        result = await session.execute(
            select(StickySession).where(
                StickySession.key == sticky_key,
                StickySession.kind == StickySessionKind.CODEX_SESSION,
            )
        )
        sticky = result.scalar_one()

    expected_account_id = "anthropic-a" if seen_authorizations[0].endswith("anthropic-access-a") else "anthropic-b"
    assert sticky.account_id == expected_account_id
