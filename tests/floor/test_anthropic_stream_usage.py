from __future__ import annotations

import json
from datetime import timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import select

import app.modules.proxy.anthropic_service as anthropic_proxy_module
from app.core.utils.time import utcnow
from app.db.models import RequestLog
from app.db.session import SessionLocal
from tests.floor.anthropic_proxy_support import (
    ANTHROPIC_SSE_BYTES,
    _disable_pool_exhausted_wait,
    _FakeResponse,
    _FakeResponseContext,
    _insert_account,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_anthropic_messages_mid_stream_failure_emits_sse_error_event(async_client, monkeypatch):

    _disable_pool_exhausted_wait(monkeypatch)
    await _insert_account(
        account_id="anthropic-only",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )
    reset_at = int((utcnow() + timedelta(minutes=9)).replace(tzinfo=timezone.utc).timestamp())

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers, json_body
        return _FakeResponseContext(
            _FakeResponse(
                429,
                b'{"error":{"message":"top model cooldown"}}',
                headers={"anthropic-ratelimit-unified-reset": str(reset_at)},
            )
        )

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

    text = body.decode("utf-8")
    assert "event: error\n" in text
    error_data = json.loads(text.split("event: error\ndata: ", 1)[1].split("\n\n", 1)[0])
    assert error_data["type"] == "error"
    assert error_data["error"]["type"] == "rate_limit_error"


@pytest.mark.asyncio
async def test_anthropic_messages_streams_sse_and_logs_usage(async_client, monkeypatch):
    await _insert_account(
        account_id="openai-account",
        provider="openai",
        access_token="openai-access",
        email="openai@example.com",
    )
    await _insert_account(
        account_id="anthropic-account",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )

    captured: dict[str, Any] = {}

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session
        assert provider_name == "anthropic"
        captured["headers"] = dict(headers)
        captured["json_body"] = dict(json_body)
        split_at = ANTHROPIC_SSE_BYTES.index(b"event: content_block_start")
        return _FakeResponseContext(
            _FakeResponse(
                200,
                ANTHROPIC_SSE_BYTES,
                chunks=[ANTHROPIC_SSE_BYTES[:split_at], ANTHROPIC_SSE_BYTES[split_at:]],
                delay_before_chunk={1: 0.05},
            )
        )

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 32,
        "stream": True,
        "system": "Claude Code system prompt prefix must pass through untouched.",
        "messages": [
            {"role": "user", "content": "hello"},
            {"role": "system", "content": "Runtime system context injected by Claude Code."},
        ],
        "thinking": {"type": "adaptive"},
        "metadata": {
            "user_id": (
                '{"device_id":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",'
                '"account_uuid":"","session_id":"a38d23ac-2d2f-4354-8861-5b686809b2b5"}'
            )
        },
        "context_management": {"edits": [{"type": "clear_tool_uses_20250919"}]},
        "output_config": {"container": {"type": "auto"}},
    }
    async with async_client.stream(
        "POST",
        "/v1/messages",
        json=payload,
        headers={
            "anthropic-beta": "oauth-2025-04-20",
            "authorization": "Bearer client-token",
            "x-api-key": "client-placeholder",
            "user-agent": "claude-cli/2.1.210 (external, sdk-cli)",
            "x-claude-code-session-id": "header-session",
        },
    ) as response:
        assert response.status_code == 200
        body = await response.aread()

    assert body == ANTHROPIC_SSE_BYTES
    assert captured["headers"]["Authorization"] == "Bearer anthropic-access"
    assert captured["headers"]["anthropic-beta"] == "oauth-2025-04-20"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert "x-api-key" not in {key.lower() for key in captured["headers"]}

    assert captured["json_body"]["system"] == [
        {"type": "text", "text": "You are Claude Code, Anthropic's official CLI for Claude."},
        {"type": "text", "text": payload["system"]},
    ]
    assert captured["json_body"]["messages"] == payload["messages"]
    assert captured["json_body"]["thinking"] == payload["thinking"]
    assert captured["json_body"]["context_management"] == payload["context_management"]
    assert captured["json_body"]["output_config"] == payload["output_config"]

    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog))
        logs = list(result.scalars())

    assert len(logs) == 1
    log = logs[0]
    assert log.provider == "anthropic"
    assert log.account_id == "anthropic-account"
    assert log.status == "success"
    assert log.input_tokens == 10
    assert log.output_tokens == 5
    assert log.cache_creation_tokens == 3
    assert log.cache_read_tokens == 4
    assert log.session_id == "a38d23ac-2d2f-4354-8861-5b686809b2b5"
    assert log.useragent == "claude-cli/2.1.210 (external, sdk-cli)"
    assert log.useragent_group == "claude-cli"
    assert log.cost_usd is not None
    assert log.latency_first_token_ms is not None
    assert log.latency_first_token_ms >= 50


@pytest.mark.asyncio
async def test_anthropic_upstream_stream_error_event_is_logged_as_error(async_client, monkeypatch):

    await _insert_account(
        account_id="anthropic-account",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )

    upstream_bytes = (
        b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_123","type":"message",'
        b'"role":"assistant","model":"claude-fable-5","content":[],"usage":{"input_tokens":10}}}\n\n'
        b'event: content_block_start\ndata: {"type":"content_block_start","index":0,'
        b'"content_block":{"type":"text","text":""}}\n\n'
        b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
        b'"delta":{"type":"text_delta","text":"partial"}}\n\n'
        b'event: error\ndata: {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}\n\n'
    )

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, headers, json_body
        return _FakeResponseContext(_FakeResponse(200, upstream_bytes))

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
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    ) as response:
        assert response.status_code == 200
        body = await response.aread()

    assert body == upstream_bytes

    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog))
        logs = list(result.scalars())
    assert len(logs) == 1
    log = logs[0]
    assert log.account_id == "anthropic-account"
    assert log.status == "error"
    assert log.error_code == "stream_error_overloaded_error"
    assert log.error_message == "Overloaded"
    assert log.input_tokens == 10
