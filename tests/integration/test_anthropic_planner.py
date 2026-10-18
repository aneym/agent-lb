from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

import app.modules.proxy.anthropic_service as anthropic_proxy_module
import app.modules.proxy.api as proxy_api_module
from app.db.models import RequestLog
from app.db.session import SessionLocal
from app.modules.api_keys.service import ApiKeyUsageReservationData
from tests.integration.anthropic_proxy_support import (
    ANTHROPIC_SSE_BYTES,
    _FakeResponse,
    _FakeResponseContext,
    _insert_account,
    _override_proxy_api_key,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_planner_messages_resolve_to_opus(
    async_client,
    monkeypatch,
):
    await _insert_account(
        account_id="anthropic-planner-messages",
        provider="anthropic",
        access_token="anthropic-access-planner-messages",
        email="planner-messages@example.com",
    )
    captured: dict[str, Any] = {}

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers
        captured["model"] = json_body["model"]
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_SSE_BYTES))

    async def fake_finalize_api_key_reservation(self, reservation, *, model, usage, cache_creation_tier=None):
        del self, reservation, usage
        captured["settled_model"] = model

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )
    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_finalize_api_key_reservation",
        fake_finalize_api_key_reservation,
    )

    async with async_client.stream(
        "POST",
        "/v1/messages",
        json={
            "model": "claude-planner",
            "max_tokens": 32,
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    ) as response:
        assert response.status_code == 200
        await response.aread()

    assert captured["model"] == "claude-opus-5-5"
    assert captured["settled_model"] == "claude-opus-5-5"
    async with SessionLocal() as session:
        log = (await session.execute(select(RequestLog))).scalar_one()
    assert log.model == "claude-opus-5-5"
    assert log.cost_usd is not None


@pytest.mark.asyncio
async def test_planner_api_key_allows_effective_opus(async_client, app_instance, monkeypatch):
    await _insert_account(
        account_id="anthropic-planner-key-opus",
        provider="anthropic",
        access_token="anthropic-access-planner-key-opus",
        email="planner-key-opus@example.com",
    )
    api_key = _override_proxy_api_key(app_instance, allowed_models=["claude-opus-5-5"])
    captured: dict[str, Any] = {}

    async def fake_enforce_request_limits(*args, **kwargs):
        del args, kwargs
        return None

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers
        captured["model"] = json_body["model"]
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_SSE_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )
    monkeypatch.setattr(proxy_api_module, "_enforce_request_limits", fake_enforce_request_limits)

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-planner",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        },
        headers={"Authorization": f"Bearer {api_key.key_prefix}"},
    )

    assert response.status_code == 200
    assert captured["model"] == "claude-opus-5-5"


@pytest.mark.asyncio
async def test_planner_api_key_rejects_disallowed_effective_model(async_client, app_instance):
    await _insert_account(
        account_id="anthropic-planner-key-disallowed",
        provider="anthropic",
        access_token="anthropic-access-planner-key-disallowed",
        email="planner-key-disallowed@example.com",
    )
    api_key = _override_proxy_api_key(app_instance, allowed_models=["claude-fable-5-1"])

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-planner",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        },
        headers={"Authorization": f"Bearer {api_key.key_prefix}"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "model_not_allowed"
    assert "claude-opus-5-5" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_planner_api_key_reservation_and_settlement_use_effective_model(
    async_client,
    app_instance,
    monkeypatch,
):
    await _insert_account(
        account_id="anthropic-planner-key-reservation",
        provider="anthropic",
        access_token="anthropic-access-planner-key-reservation",
        email="planner-key-reservation@example.com",
    )
    api_key = _override_proxy_api_key(app_instance, allowed_models=["claude-opus-5-5"])
    captured: dict[str, Any] = {}

    async def fake_enforce_request_limits(api_key, *, request_model, request_service_tier, request_usage_budget):
        del request_service_tier, request_usage_budget
        captured["reserved_model"] = request_model
        return ApiKeyUsageReservationData(
            reservation_id="planner-reservation",
            key_id=api_key.id,
            model=request_model,
        )

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers
        captured["upstream_model"] = json_body["model"]
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_SSE_BYTES))

    async def fake_finalize_api_key_reservation(self, reservation, *, model, usage, cache_creation_tier=None):
        del self, usage
        captured["settled_model"] = model
        captured["settled_reservation_model"] = reservation.model

    monkeypatch.setattr(proxy_api_module, "_enforce_request_limits", fake_enforce_request_limits)
    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )
    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_finalize_api_key_reservation",
        fake_finalize_api_key_reservation,
    )

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-planner",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        },
        headers={"Authorization": f"Bearer {api_key.key_prefix}"},
    )

    assert response.status_code == 200

    assert captured == {
        "reserved_model": "claude-opus-5-5",
        "upstream_model": "claude-opus-5-5",
        "settled_model": "claude-opus-5-5",
        "settled_reservation_model": "claude-opus-5-5",
    }


@pytest.mark.asyncio
async def test_planner_count_tokens_api_key_allows_effective_opus(
    async_client,
    app_instance,
    monkeypatch,
):
    await _insert_account(
        account_id="anthropic-planner-count-key-opus",
        provider="anthropic",
        access_token="anthropic-access-planner-count-key-opus",
        email="planner-count-key-opus@example.com",
    )
    api_key = _override_proxy_api_key(app_instance, allowed_models=["claude-opus-5-5"])
    captured: dict[str, Any] = {}

    def fake_open_count_tokens_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers
        captured["model"] = json_body["model"]
        return _FakeResponseContext(
            _FakeResponse(200, b'{"input_tokens": 17}', headers={"content-type": "application/json"})
        )

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_count_tokens_response",
        fake_open_count_tokens_response,
    )

    response = await async_client.post(
        "/v1/messages/count_tokens",
        json={"model": "claude-planner", "messages": [{"role": "user", "content": "hello"}]},
        headers={"Authorization": f"Bearer {api_key.key_prefix}"},
    )

    assert response.status_code == 200
    assert response.json() == {"input_tokens": 17}
    assert captured["model"] == "claude-opus-5-5"


@pytest.mark.asyncio
async def test_planner_count_tokens_api_key_rejects_disallowed_effective_model(
    async_client,
    app_instance,
):
    await _insert_account(
        account_id="anthropic-planner-count-key-disallowed",
        provider="anthropic",
        access_token="anthropic-access-planner-count-key-fable",
        email="planner-count-key-disallowed@example.com",
    )
    api_key = _override_proxy_api_key(app_instance, allowed_models=["claude-fable-5-1"])

    response = await async_client.post(
        "/v1/messages/count_tokens",
        json={"model": "claude-planner", "messages": [{"role": "user", "content": "hello"}]},
        headers={"Authorization": f"Bearer {api_key.key_prefix}"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "model_not_allowed"
    assert "claude-opus-5-5" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_planner_count_tokens_resolves_to_opus(
    async_client,
    monkeypatch,
):
    await _insert_account(
        account_id="anthropic-planner-count",
        provider="anthropic",
        access_token="anthropic-access-planner-count-exhausted",
        email="planner-count@example.com",
    )
    captured: dict[str, Any] = {}

    def fake_open_count_tokens_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers
        captured["model"] = json_body["model"]
        return _FakeResponseContext(
            _FakeResponse(200, b'{"input_tokens": 17}', headers={"content-type": "application/json"})
        )

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_count_tokens_response",
        fake_open_count_tokens_response,
    )

    response = await async_client.post(
        "/v1/messages/count_tokens",
        json={"model": "claude-planner", "messages": [{"role": "user", "content": "hello"}]},
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 200
    assert response.json() == {"input_tokens": 17}
    assert captured["model"] == "claude-opus-5-5"
