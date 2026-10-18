from __future__ import annotations

import pytest

from tests.integration.openai_compat_support import (
    _completed_event as _completed_event,
)
from tests.integration.openai_compat_support import (
    _import_account as _import_account,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_v1_chat_completions_rejects_non_text_developer(async_client):
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {
                "role": "developer",
                "content": [{"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}],
            },
            {"role": "user", "content": "hi"},
        ],
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_v1_chat_completions_rejects_invalid_audio(async_client):
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "input_audio", "input_audio": {"data": "AAA", "format": "ogg"}},
                ],
            }
        ],
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_v1_chat_completions_rejects_strict_schema_violation(async_client):

    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "Return JSON."}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "result_schema",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                },
            },
        },
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "invalid_json_schema"
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["param"] == "text.format.schema"
    assert "additionalProperties" in body["error"]["message"]
    assert "result_schema" in body["error"]["message"]


@pytest.mark.asyncio
async def test_v1_chat_completions_rejects_strict_function_tool_violation(async_client):

    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "Weather in Seoul?"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "x",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                    "strict": True,
                },
            }
        ],
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "invalid_function_parameters"
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["param"] == "tools[0].function.parameters"
    assert "get_weather" in body["error"]["message"]


@pytest.mark.asyncio
async def test_v1_chat_completions_strict_violation_param_uses_original_index(async_client):

    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "Weather in Seoul?"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "description": "no name → dropped",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "x",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                    "strict": True,
                },
            },
        ],
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "invalid_function_parameters"
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["param"] == "tools[1].function.parameters"
    assert "get_weather" in body["error"]["message"]


@pytest.mark.asyncio
async def test_v1_chat_completions_strict_violation_when_type_omitted_in_chat_tool(async_client):

    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "Weather in Seoul?"}],
        "tools": [
            {
                "function": {
                    "name": "get_weather",
                    "description": "x",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                    "strict": True,
                },
            }
        ],
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "invalid_function_parameters"
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["param"] == "tools[0].function.parameters"
    assert "get_weather" in body["error"]["message"]


@pytest.mark.asyncio
async def test_v1_chat_completions_responses_shaped_input_rejects_flat_strict_tool(async_client):

    payload = {
        "model": "gpt-5.2",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Weather in Seoul?"}]}],
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
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "invalid_function_parameters"
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["param"] == "tools[0].parameters"
    assert "get_weather" in body["error"]["message"]


@pytest.mark.asyncio
async def test_v1_chat_completions_rejects_missing_json_schema(async_client):
    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "Return JSON."}],
        "response_format": {"type": "json_schema"},
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_v1_chat_completions_rejects_file_id(async_client):
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Summarize file."},
                    {"type": "file", "file": {"file_id": "file-123"}},
                ],
            }
        ],
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 400
    payload = resp.json()
    assert payload["error"]["type"] == "invalid_request_error"
    assert payload["error"]["message"] == "Invalid request payload"
    assert payload["error"]["param"] == "messages"


@pytest.mark.asyncio
async def test_v1_chat_completions_rejects_audio_input(async_client):
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Transcribe audio."},
                    {"type": "input_audio", "input_audio": {"data": "AAA", "format": "wav"}},
                ],
            }
        ],
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_v1_chat_completions_rejects_builtin_tools(async_client):
    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "Search the web."}],
        "tools": [{"type": "image_generation"}],
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 400
