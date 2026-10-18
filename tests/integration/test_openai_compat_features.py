from __future__ import annotations

from typing import cast

import pytest

import app.modules.proxy.service as proxy_module
from app.core.openai.requests import ResponsesRequest
from tests.integration.openai_compat_support import (
    _completed_event as _completed_event,
)
from tests.integration.openai_compat_support import (
    _import_account as _import_account,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_v1_responses_forwards_input_file_url(async_client, monkeypatch):
    await _import_account(async_client, "acc_file_url", "file-url@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_file_url")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Summarize this file."},
                    {"type": "input_file", "file_url": "https://example.com/file.pdf"},
                ],
            }
        ],
    }
    resp = await async_client.post("/v1/responses", json=payload)
    assert resp.status_code == 200
    assert seen["payload"].input == payload["input"]


@pytest.mark.asyncio
async def test_v1_responses_forwards_input_file_id(async_client, monkeypatch):

    await _import_account(async_client, "acc_input_file_id", "input-file-id@example.com")

    seen: dict[str, object] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_input_file_id")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Summarize this file."},
                    {"type": "input_file", "file_id": "file-123"},
                ],
            }
        ],
    }
    resp = await async_client.post("/v1/responses", json=payload)
    assert resp.status_code == 200
    forwarded_input = cast(ResponsesRequest, seen["payload"]).input
    assert forwarded_input == payload["input"]


@pytest.mark.asyncio
async def test_v1_responses_accepts_previous_response_id(async_client, monkeypatch):
    await _import_account(async_client, "acc_prev_response_id", "prev-response-id@example.com")
    seen_previous_response_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        del headers, access_token, account_id, base_url, raise_for_status, _kw
        seen_previous_response_ids.append(getattr(payload, "previous_response_id", None))
        yield 'data: {"type":"response.completed","response":{"id":"resp_abc123"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "previous_response_id": "resp_abc123",
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": "Continue."}],
            }
        ],
        "stream": True,
    }
    resp = await async_client.post("/v1/responses", json=payload)
    assert resp.status_code == 200
    assert seen_previous_response_ids == ["resp_abc123"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_payload",
    [
        {"type": "file_search", "vector_store_ids": ["vs_dummy"]},
        {"type": "code_interpreter", "container": {"type": "auto"}},
        {
            "type": "computer_use_preview",
            "display_width": 1024,
            "display_height": 768,
            "environment": "browser",
        },
        {"type": "image_generation"},
    ],
)
async def test_v1_responses_forwards_builtin_tools(async_client, monkeypatch, tool_payload):
    await _import_account(async_client, "acc_builtin_tools", "builtin-tools@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        del headers, access_token, account_id, base_url, raise_for_status
        seen["payload"] = payload
        yield _completed_event("resp_builtin_tools")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    request_payload = {
        "model": "gpt-5.2",
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": "Run tool."}],
            }
        ],
        "tools": [tool_payload],
    }

    resp = await async_client.post("/v1/responses", json=request_payload)
    assert resp.status_code == 200
    assert seen["payload"].tools == [tool_payload]


@pytest.mark.asyncio
async def test_v1_responses_forwards_input_string(async_client, monkeypatch):
    await _import_account(async_client, "acc_input_string", "input-string@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_input_string")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.2", "input": "Hello"}
    resp = await async_client.post("/v1/responses", json=payload)
    assert resp.status_code == 200
    assert seen["payload"].input == [
        {"role": "user", "content": [{"type": "input_text", "text": "Hello"}]},
    ]


@pytest.mark.asyncio
async def test_v1_responses_forwards_include_logprobs(async_client, monkeypatch):
    await _import_account(async_client, "acc_include_logprobs", "include-logprobs@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_include")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "include": ["message.output_text.logprobs"],
    }
    resp = await async_client.post("/v1/responses", json=payload)
    assert resp.status_code == 200
    assert seen["payload"].include == ["message.output_text.logprobs"]


@pytest.mark.asyncio
async def test_v1_responses_preserves_prompt_cache_controls(async_client, monkeypatch):
    await _import_account(async_client, "acc_prompt_cache_v1", "prompt-cache-v1@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload.to_payload()
        yield _completed_event("resp_prompt_cache_v1")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "input": "cache me",
        "prompt_cache_key": "thread_123",
        "prompt_cache_retention": "4h",
    }
    resp = await async_client.post("/v1/responses", json=payload)
    assert resp.status_code == 200
    assert seen["payload"]["prompt_cache_key"] == "thread_123"
    assert "prompt_cache_retention" not in seen["payload"]


@pytest.mark.asyncio
async def test_v1_responses_normalizes_prompt_cache_aliases(async_client, monkeypatch):
    await _import_account(async_client, "acc_prompt_cache_alias", "prompt-cache-alias@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload.to_payload()
        yield _completed_event("resp_prompt_cache_alias")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "input": "cache me",
        "promptCacheKey": "thread_alias",
        "promptCacheRetention": "12h",
    }
    resp = await async_client.post("/v1/responses", json=payload)
    assert resp.status_code == 200
    assert seen["payload"]["prompt_cache_key"] == "thread_alias"
    assert "prompt_cache_retention" not in seen["payload"]
    assert "promptCacheKey" not in seen["payload"]
    assert "promptCacheRetention" not in seen["payload"]


@pytest.mark.asyncio
async def test_backend_responses_forwards_service_tier(async_client, monkeypatch):
    await _import_account(async_client, "acc_backend_service_tier", "backend-service-tier@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_backend_service_tier")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    request_payload = {
        "model": "gpt-5.2",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Fast"}]}],
        "service_tier": "priority",
    }
    resp = await async_client.post("/backend-api/codex/responses", json=request_payload)
    assert resp.status_code == 200
    assert seen["payload"].service_tier == "priority"


@pytest.mark.asyncio
async def test_backend_responses_normalizes_fast_service_tier_for_upstream(async_client, monkeypatch):
    await _import_account(async_client, "acc_backend_fast_tier", "backend-fast-tier@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload.to_payload()
        yield _completed_event("resp_backend_fast_tier")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    request_payload = {
        "model": "gpt-5.2",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Fast"}]}],
        "service_tier": "fast",
    }
    resp = await async_client.post("/backend-api/codex/responses", json=request_payload)
    assert resp.status_code == 200
    assert seen["payload"]["service_tier"] == "priority"


@pytest.mark.asyncio
async def test_v1_responses_rejects_invalid_include(async_client):
    payload = {"model": "gpt-5.2", "input": "hi", "include": ["not_allowed"]}
    resp = await async_client.post("/v1/responses", json=payload)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_v1_responses_coerces_store_true_to_false(async_client):

    payload = {"model": "gpt-5.2", "input": "hi", "store": True}
    resp = await async_client.post("/v1/responses", json=payload)

    assert resp.status_code != 400


@pytest.mark.asyncio
@pytest.mark.parametrize("truncation", ["auto", "disabled"])
async def test_v1_responses_accepts_truncation(async_client, truncation):
    payload = {"model": "gpt-5.2", "input": "hi", "truncation": truncation}
    resp = await async_client.post("/v1/responses", json=payload)

    assert resp.status_code != 400


@pytest.mark.asyncio
async def test_v1_responses_rejects_conversation_and_previous(async_client):
    payload = {
        "model": "gpt-5.2",
        "input": "hi",
        "conversation": "conv_1",
        "previous_response_id": "resp_1",
    }
    resp = await async_client.post("/v1/responses", json=payload)
    assert resp.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_type", ["web_search", "web_search_preview"])
async def test_v1_responses_allows_web_search(async_client, monkeypatch, tool_type):
    await _import_account(async_client, "acc_web_search", "web-search@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_web_search")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    request_payload = {
        "model": "gpt-5.2",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Search"}]}],
        "tools": [{"type": tool_type}],
    }
    resp = await async_client.post("/v1/responses", json=request_payload)
    assert resp.status_code == 200
    assert seen["payload"].tools == [{"type": "web_search"}]


@pytest.mark.asyncio
async def test_v1_responses_preserves_image_generation_builtin_tool(async_client, monkeypatch):
    await _import_account(async_client, "acc_v1_image_gen", "v1-image-gen@example.com")

    seen = {}
    image_tool = {"type": "image_generation", "output_format": "png"}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_v1_image_generation")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    request_payload = {
        "model": "gpt-5.2",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Draw?"}]}],
        "tools": [image_tool],
    }
    resp = await async_client.post("/v1/responses", json=request_payload)
    assert resp.status_code == 200
    assert seen["payload"].tools == [image_tool]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_type", ["web_search", "web_search_preview"])
async def test_backend_responses_allows_web_search(async_client, monkeypatch, tool_type):
    await _import_account(async_client, "acc_backend_web_search", "backend-web-search@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_backend_web_search")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    request_payload = {
        "model": "gpt-5.2",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Search"}]}],
        "tools": [{"type": tool_type}],
    }
    resp = await async_client.post("/backend-api/codex/responses", json=request_payload)
    assert resp.status_code == 200
    assert seen["payload"].tools == [{"type": "web_search"}]


@pytest.mark.asyncio
async def test_backend_responses_preserves_image_generation_tool_advertisement(async_client, monkeypatch):
    await _import_account(async_client, "acc_backend_image_gen", "backend-image-gen@example.com")

    seen = {}
    function_tool = {
        "type": "function",
        "name": "lookup_weather",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
    }

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_backend_image_generation")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    request_payload = {
        "model": "gpt-5.2",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Weather?"}]}],
        "tools": [{"type": "image_generation", "output_format": "png"}, function_tool],
    }
    resp = await async_client.post("/backend-api/codex/responses", json=request_payload)
    assert resp.status_code == 200
    assert seen["payload"].tools == [{"type": "image_generation", "output_format": "png"}, function_tool]


@pytest.mark.asyncio
async def test_backend_responses_preserve_explicit_image_generation_tool_choice(async_client, monkeypatch):
    await _import_account(async_client, "acc_backend_explicit_image_gen", "backend-explicit-image-gen@example.com")

    seen = {}
    image_tool = {"type": "image_generation", "output_format": "png"}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_backend_explicit_image_generation")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    request_payload = {
        "model": "gpt-5.2",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Draw?"}]}],
        "tools": [image_tool],
        "tool_choice": {"type": "image_generation"},
    }
    resp = await async_client.post("/backend-api/codex/responses", json=request_payload)

    assert resp.status_code == 200
    assert seen["payload"].tools == [image_tool]
    assert seen["payload"].tool_choice == {"type": "image_generation"}


@pytest.mark.asyncio
async def test_backend_responses_preserve_required_image_generation_tool_choice(async_client, monkeypatch):
    await _import_account(async_client, "acc_backend_required_image_gen", "backend-required-image-gen@example.com")

    seen = {}
    image_tool = {"type": "image_generation", "output_format": "png"}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_backend_required_image_generation")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    request_payload = {
        "model": "gpt-5.2",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Draw?"}]}],
        "tools": [image_tool],
        "tool_choice": "required",
    }
    resp = await async_client.post("/backend-api/codex/responses", json=request_payload)

    assert resp.status_code == 200
    assert seen["payload"].tools == [image_tool]
    assert seen["payload"].tool_choice == "required"


@pytest.mark.asyncio
async def test_v1_responses_rejects_strict_function_tool_violation(async_client):

    payload = {
        "model": "gpt-5.2",
        "input": [{"role": "user", "content": "Weather in Seoul?"}],
        "tools": [
            {
                "type": "function",
                "name": "get_weather",
                "description": "x",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
                "strict": True,
            }
        ],
    }
    resp = await async_client.post("/v1/responses", json=payload)
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "invalid_function_parameters"
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["param"] == "tools[0].parameters"
    assert "get_weather" in body["error"]["message"]


@pytest.mark.asyncio
async def test_backend_responses_rejects_strict_schema_violation_with_specific_error(async_client):

    payload = {
        "model": "gpt-5.2",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Return JSON."}]}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "result_schema",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                },
            }
        },
    }
    resp = await async_client.post("/backend-api/codex/responses", json=payload)
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "invalid_json_schema"
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["param"] == "text.format.schema"
    assert "additionalProperties" in body["error"]["message"]
