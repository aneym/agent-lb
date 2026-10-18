from __future__ import annotations

import pytest

import app.modules.proxy.service as proxy_module
from tests.integration.openai_compat_support import (
    _completed_event as _completed_event,
)
from tests.integration.openai_compat_support import (
    _import_account as _import_account,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_v1_chat_completions_maps_response_format(async_client, monkeypatch):
    await _import_account(async_client, "acc_chat_format", "chat-format@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_chat_format")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "Return JSON."}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "result_schema",
                "schema": {
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    text = seen["payload"].text
    assert text is not None
    assert text.format is not None
    assert text.format.type == "json_schema"
    assert text.format.name == "result_schema"


@pytest.mark.asyncio
async def test_v1_chat_completions_responses_shaped_input_preserves_mcp_tool(async_client, monkeypatch):
    await _import_account(async_client, "acc_chat_mcp_tool", "chat-mcp-tool@example.com")
    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_chat_mcp_tool")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    tool = {
        "type": "mcp",
        "server_label": "filesystem",
        "server_url": "https://example.com/mcp",
        "require_approval": "never",
    }
    payload = {
        "model": "gpt-5.2",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Run tool."}]}],
        "tools": [tool],
        "tool_choice": {"type": "mcp", "server_label": "filesystem"},
    }

    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    assert seen["payload"].tools == [tool]
    assert seen["payload"].tool_choice == {"type": "mcp", "server_label": "filesystem"}


@pytest.mark.asyncio
async def test_v1_chat_completions_forwards_multimodal(async_client, monkeypatch):
    await _import_account(async_client, "acc_chat_multi", "chat-multi@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_chat_multi")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Check image and audio."},
                    {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
                    {"type": "file", "file": {"file_url": "https://example.com/file.pdf"}},
                ],
            }
        ],
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    assert isinstance(seen["payload"].input, list)
    assert seen["payload"].input[0]["role"] == "user"
    content = seen["payload"].input[0]["content"]
    assert content[0] == {"type": "input_text", "text": "Check image and audio."}
    assert content[1] == {"type": "input_image", "image_url": "https://example.com/a.png"}
    assert content[2] == {"type": "input_file", "file_url": "https://example.com/file.pdf"}


@pytest.mark.asyncio
async def test_v1_chat_completions_accepts_responses_shaped_builtin_tools(async_client, monkeypatch):
    await _import_account(async_client, "acc_chat_builtin_tools", "chat-builtin-tools@example.com")

    seen = {}
    image_tool = {"type": "image_generation", "output_format": "png"}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        del headers, access_token, account_id, base_url, raise_for_status
        seen["payload"] = payload
        yield _completed_event("resp_chat_builtin_tools")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": "Generate an image."}],
            }
        ],
        "tools": [image_tool],
        "tool_choice": {"type": "image_generation"},
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    assert seen["payload"].tools == [image_tool]
    assert seen["payload"].tool_choice == {"type": "image_generation"}


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_type", ["web_search", "web_search_preview"])
async def test_v1_chat_completions_allows_web_search(async_client, monkeypatch, tool_type):
    await _import_account(async_client, "acc_chat_web_search", "chat-web-search@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_chat_web_search")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "Search the web."}],
        "tools": [{"type": tool_type}],
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    assert seen["payload"].tools == [{"type": "web_search"}]


@pytest.mark.asyncio
async def test_v1_chat_completions_normalizes_tools_and_tool_choice(async_client, monkeypatch):
    await _import_account(async_client, "acc_chat_tools", "chat-tools@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_chat_tools")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "Weather?"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
            }
        ],
        "tool_choice": {"type": "function", "function": {"name": "get_weather"}},
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    assert seen["payload"].tools == [
        {
            "type": "function",
            "name": "get_weather",
            "description": "Get weather",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
        }
    ]
    assert seen["payload"].tool_choice == {"type": "function", "name": "get_weather"}


@pytest.mark.asyncio
async def test_v1_chat_completions_does_not_enable_codex_session_affinity(async_client, monkeypatch):
    await _import_account(async_client, "acc_chat_affinity_a", "chat-affinity-a@example.com")
    await _import_account(async_client, "acc_chat_affinity_b", "chat-affinity-b@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["account_id"] = account_id
        seen["prompt_cache_key"] = getattr(payload, "prompt_cache_key", None)
        yield _completed_event("resp_chat_affinity")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "Weather?"}],
    }
    resp = await async_client.post("/v1/chat/completions", json=payload, headers={"session_id": "chat-session-123"})
    assert resp.status_code == 200
    assert isinstance(seen["prompt_cache_key"], str)
    assert seen["prompt_cache_key"]


@pytest.mark.asyncio
async def test_v1_chat_completions_maps_reasoning_effort(async_client, monkeypatch):
    await _import_account(async_client, "acc_chat_reason", "chat-reason@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_chat_reason")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "Think."}],
        "reasoning_effort": "low",
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    assert seen["payload"].reasoning is not None
    assert seen["payload"].reasoning.effort == "low"


@pytest.mark.asyncio
async def test_v1_chat_completions_normalizes_enable_thinking(async_client, monkeypatch):
    await _import_account(async_client, "acc_chat_enable_thinking", "chat-enable-thinking@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload.to_payload()
        yield _completed_event("resp_chat_enable_thinking")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "Think."}],
        "enable_thinking": True,
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    assert seen["payload"]["reasoning"] == {"effort": "medium"}
    assert "enable_thinking" not in seen["payload"]


@pytest.mark.asyncio
async def test_v1_chat_completions_forwards_service_tier(async_client, monkeypatch):
    await _import_account(async_client, "acc_chat_service_tier", "chat-service-tier@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_chat_service_tier")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "Think fast."}],
        "service_tier": "priority",
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    assert seen["payload"].service_tier == "priority"


@pytest.mark.asyncio
async def test_v1_chat_completions_preserves_prompt_cache_controls(async_client, monkeypatch):
    await _import_account(async_client, "acc_chat_prompt_cache", "chat-prompt-cache@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload.to_payload()
        yield _completed_event("resp_chat_prompt_cache")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "Cache this chat."}],
        "prompt_cache_key": "chat_thread_123",
        "prompt_cache_retention": "8h",
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    assert seen["payload"]["prompt_cache_key"] == "chat_thread_123"
    assert "prompt_cache_retention" not in seen["payload"]
