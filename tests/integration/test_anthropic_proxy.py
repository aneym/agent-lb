from __future__ import annotations

import pytest

import app.modules.proxy.anthropic_service as anthropic_proxy_module
from app.core.anthropic.models import AnthropicMessageRequest
from tests.integration.anthropic_proxy_support import (
    _FakeResponse,
    _insert_account,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_anthropic_messages_returns_error_when_no_accounts_available(async_client):
    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "type": "error",
        "error": {
            "type": "no_available_anthropic_accounts",
            "message": ("No available Anthropic accounts"),
        },
    }


@pytest.mark.asyncio
async def test_session_route_defaults_omitted_model_to_opus_5(async_client):
    await _insert_account(
        account_id="anthropic-opus-default",
        provider="anthropic",
        access_token="anthropic-access-opus-default",
        email="opus-default@example.com",
    )

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={"sessionId": "session-opus-default"},
    )

    assert response.status_code == 200
    assert response.json()["model"] == "claude-opus-5-5"
    assert response.json()["quotaKey"] == "anthropic_top_thinking"
    assert response.json()["affinityQuotaKey"] == "anthropic_top_thinking"


def test_anthropic_rate_limit_error_parses_unified_reset_headers():
    response = _FakeResponse(
        429,
        b'{"error":{"message":"rate limited"}}',
        headers={
            "anthropic-ratelimit-unified-reset": "2026-06-09T19:50:00.000000+00:00",
            "retry-after": "37",
        },
    )

    error = anthropic_proxy_module._rate_limit_error_from_response(response, "rate limited")

    assert error["resets_at"] == 1781034600
    assert error["resets_in_seconds"] == 37


def test_anthropic_rate_limit_error_parses_epoch_reset_headers():
    response = _FakeResponse(
        429,
        b'{"error":{"message":"rate limited"}}',
        headers={"anthropic-ratelimit-unified-reset": "1781034600"},
    )

    error = anthropic_proxy_module._rate_limit_error_from_response(response, "rate limited")

    assert error["resets_at"] == 1781034600


def test_anthropic_rate_limit_error_parses_fast_reset_headers():
    response = _FakeResponse(
        429,
        b'{"error":{"message":"fast pool exhausted"}}',
        headers={"anthropic-fast-input-tokens-reset": "1781034600"},
    )

    error = anthropic_proxy_module._rate_limit_error_from_response(response, "fast pool exhausted")

    assert error["resets_at"] == 1781034600


def test_anthropic_sticky_key_uses_quota_scoped_session_hash():
    payload = AnthropicMessageRequest.model_validate(
        {
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        }
    )

    key = anthropic_proxy_module._anthropic_sticky_key(
        payload,
        {"x-claude-session-id": "session-123"},
        quota_key=anthropic_proxy_module._anthropic_quota_key(payload),
    )

    assert key is not None
    assert key.startswith("claude:anthropic_top_thinking:session:")
    assert "session-123" not in key


def test_anthropic_fast_mode_uses_fast_quota_and_model_affinity():
    payload = AnthropicMessageRequest.model_validate(
        {
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "speed": "fast",
            "thinking": {"type": "adaptive"},
        }
    )

    quota_key = anthropic_proxy_module._messages_quota_key(payload, provider_name="anthropic")
    affinity_quota_key = anthropic_proxy_module._messages_affinity_quota_key(payload, provider_name="anthropic")
    key = anthropic_proxy_module._messages_sticky_key(
        payload,
        {"x-claude-session-id": "session-123"},
        provider_name="anthropic",
        quota_key=affinity_quota_key,
    )

    assert quota_key == "anthropic_fast"
    assert affinity_quota_key == "anthropic_top_thinking_fable"
    assert key is not None
    assert key.startswith("claude:anthropic_top_thinking_fable:session:")


def test_anthropic_fast_beta_header_without_fast_speed_uses_model_quota():
    payload = AnthropicMessageRequest.model_validate(
        {
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "speed": "standard",
            "thinking": {"type": "adaptive"},
        }
    )

    quota_key = anthropic_proxy_module._messages_quota_key(payload, provider_name="anthropic")
    key = anthropic_proxy_module._messages_sticky_key(
        payload,
        {"anthropic-beta": "fast-mode-2026-02-01", "x-claude-session-id": "session-123"},
        provider_name="anthropic",
        quota_key=anthropic_proxy_module._messages_affinity_quota_key(payload, provider_name="anthropic"),
    )

    assert quota_key == "anthropic_top_thinking"
    assert key is not None
    assert key.startswith("claude:anthropic_top_thinking_fable:session:")


def test_messages_affinity_quota_key_scopes_fable_class_separately():
    fable_payload = AnthropicMessageRequest.model_validate(
        {
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        }
    )
    opus_payload = AnthropicMessageRequest.model_validate(
        {
            "model": "claude-opus-4-8",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        }
    )
    haiku_payload = AnthropicMessageRequest.model_validate(
        {
            "model": "claude-haiku-4-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
        }
    )

    assert (
        anthropic_proxy_module._messages_affinity_quota_key(fable_payload, provider_name="anthropic")
        == "anthropic_top_thinking_fable"
    )
    assert (
        anthropic_proxy_module._messages_affinity_quota_key(opus_payload, provider_name="anthropic")
        == "anthropic_top_thinking"
    )
    assert (
        anthropic_proxy_module._messages_affinity_quota_key(haiku_payload, provider_name="anthropic")
        == "anthropic_standard"
    )


def test_kimi_messages_derive_kimi_provider_quota_and_sticky_key():
    payload = AnthropicMessageRequest.model_validate(
        {
            "model": "k3-256k",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "enabled"},
        }
    )

    assert anthropic_proxy_module._messages_provider_name(payload) == "kimi"
    quota_key = anthropic_proxy_module._messages_quota_key(payload, provider_name="kimi")
    key = anthropic_proxy_module._messages_sticky_key(
        payload,
        {"x-claude-session-id": "session-123"},
        provider_name="kimi",
        quota_key=quota_key,
    )

    assert quota_key == "kimi_coding_thinking"
    assert anthropic_proxy_module._count_tokens_quota_key("kimi") == "kimi_count_tokens"
    assert key is not None
    assert key.startswith("kimi:kimi_coding_thinking:session:")
    assert "session-123" not in key


def test_glm_messages_derive_glm_provider_quota_and_sticky_key():
    payload = AnthropicMessageRequest.model_validate(
        {
            "model": "glm-5.2",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "enabled"},
        }
    )

    assert anthropic_proxy_module._messages_provider_name(payload) == "glm"
    quota_key = anthropic_proxy_module._messages_quota_key(payload, provider_name="glm")
    key = anthropic_proxy_module._messages_sticky_key(
        payload,
        {"x-claude-session-id": "session-123"},
        provider_name="glm",
        quota_key=quota_key,
    )

    assert quota_key == "glm_coding_thinking"
    assert key is not None
    assert key.startswith("glm:glm_coding_thinking:session:")
    assert "session-123" not in key
