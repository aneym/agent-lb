from __future__ import annotations

import json
from typing import Any

import pytest

import app.modules.proxy.anthropic_service as anthropic_proxy_module
from tests.integration.anthropic_proxy_support import (
    ANTHROPIC_SSE_BYTES,
    _FakeResponse,
    _FakeResponseContext,
    _insert_account,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_anthropic_messages_adds_claude_code_identity_for_plain_api_clients(async_client, monkeypatch):

    await _insert_account(
        account_id="anthropic-account",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )

    captured: dict[str, Any] = {}

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers
        captured["json_body"] = dict(json_body)
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
            "model": "claude-opus-5",
            "max_tokens": 32,
            "stream": True,
            "messages": [{"role": "user", "content": "say ok"}],
        },
    ) as response:
        assert response.status_code == 200
        await response.aread()

    assert captured["json_body"]["system"] == [
        {"type": "text", "text": "You are Claude Code, Anthropic's official CLI for Claude."}
    ]


@pytest.mark.asyncio
async def test_anthropic_messages_keeps_claude_code_identity_payload_untouched(async_client, monkeypatch):

    await _insert_account(
        account_id="anthropic-account",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )

    captured: dict[str, Any] = {}

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers
        captured["json_body"] = dict(json_body)
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_SSE_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    system = [
        {"type": "text", "text": "You are Claude Code, Anthropic's official CLI for Claude."},
        {"type": "text", "text": "Session context."},
    ]
    async with async_client.stream(
        "POST",
        "/v1/messages",
        json={
            "model": "claude-opus-5",
            "max_tokens": 32,
            "stream": True,
            "system": system,
            "messages": [{"role": "user", "content": "say ok"}],
        },
    ) as response:
        assert response.status_code == 200
        await response.aread()

    assert captured["json_body"]["system"] == system


@pytest.mark.asyncio
async def test_anthropic_messages_forward_claude_code_billing_first_payloads_unchanged(async_client, monkeypatch):

    await _insert_account(
        account_id="anthropic-account",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )
    forwarded = []

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers
        forwarded.append(json_body)
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_SSE_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService, "_open_upstream_response", fake_open_upstream_response
    )
    cached = {"type": "text", "text": "Stable prompt", "cache_control": {"type": "ephemeral", "ttl": "1h"}}
    sent = []
    for second_block, cch in (
        ("You are Claude Code, Anthropic's official CLI for Claude.", "7122f"),
        ("You are Claude Code, Anthropic's official CLI for Claude.", "dc618"),
        ("You are a Claude agent, built on Anthropic's Claude Agent SDK.", "60f1e"),
        ("You are a Claude agent, built on Anthropic's Claude Agent SDK.", "f8d14"),
    ):
        marker = f"x-anthropic-billing-header: cc_version=2.1.282.190; cc_entrypoint=cli; cch={cch};"
        system = [{"type": "text", "text": marker}, {"type": "text", "text": second_block}, cached]
        sent.append(system)
        async with async_client.stream(
            "POST",
            "/v1/messages",
            json={
                "model": "claude-sonnet-5",
                "max_tokens": 32,
                "stream": True,
                "metadata": {"user_id": json.dumps({"session_id": "billing-first-session"})},
                "system": system,
                "messages": [{"role": "user", "content": "reply ok"}],
            },
        ) as response:
            assert response.status_code == 200
            await response.aread()

    assert [body["system"] for body in forwarded] == sent
