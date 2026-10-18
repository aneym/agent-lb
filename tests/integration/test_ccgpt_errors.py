from __future__ import annotations

import json

import pytest
from fastapi.responses import StreamingResponse

from app.core.clients.proxy import ProxyResponseError
from app.modules.proxy import api as proxy_api

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_ccgpt_context_overflow_returns_prompt_too_long_not_api_error(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:

    overflow_envelope = {
        "error": {
            "type": "invalid_request_error",
            "code": "context_length_exceeded",
            "param": "input",
            "message": "Your input exceeds the context window of this model.",
        }
    }

    async def fake_stream(request, payload, context, api_key, **kwargs):
        return proxy_api._stream_startup_error_response(
            request,
            ProxyResponseError(400, overflow_envelope),
            headers={},
        )

    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream)

    response = await async_client.post(
        "/v1/ccgpt/messages",
        json={
            "model": "claude-opus-4-6",
            "max_tokens": 1024,
            "stream": True,
            "messages": [{"role": "user", "content": "way too much context"}],
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "invalid_request_error"
    assert "prompt is too long" in body["error"]["message"].lower()


@pytest.mark.asyncio
async def test_ccgpt_midstream_context_overflow_emits_prompt_too_long_without_success(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:

    async def fake_stream(request, payload, context, api_key, **kwargs):
        async def source():
            yield 'data: {"type":"response.created","response":{"id":"resp_live","model":"gpt-6.1-sol"}}\n\n'
            yield 'data: {"type":"response.output_text.delta","delta":"partial answer"}\n\n'
            yield (
                'data: {"type":"response.failed","response":{"error":{'
                '"code":"context_length_exceeded",'
                '"message":"Your input exceeds the context window of this model."}}}\n\n'
            )

        return StreamingResponse(source(), media_type="text/event-stream")

    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/v1/ccgpt/messages",
        json={
            "model": "claude-opus-4-6",
            "max_tokens": 1024,
            "stream": True,
            "messages": [{"role": "user", "content": "way too much context"}],
        },
    ) as response:
        body = (await response.aread()).decode()

    assert response.status_code == 200
    assert '"type":"message_start"' in body
    assert "partial answer" in body
    assert '"type":"error"' in body
    assert '"type":"invalid_request_error"' in body
    assert "prompt is too long" in body.lower()
    assert "message_stop" not in body
    assert "message_delta" not in body


@pytest.mark.asyncio
async def test_ccgpt_precontent_stream_overflow_returns_http_400(async_client, monkeypatch: pytest.MonkeyPatch) -> None:

    async def fake_stream(request, payload, context, api_key, **kwargs):
        async def source():
            yield 'data: {"type":"response.created","response":{"id":"resp_live","model":"gpt-6.1-sol"}}\n\n'
            yield (
                'data: {"type":"response.failed","response":{"error":{'
                '"code":"context_length_exceeded",'
                '"message":"Your input exceeds the context window of this model."}}}\n\n'
            )

        return StreamingResponse(source(), media_type="text/event-stream")

    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream)

    response = await async_client.post(
        "/v1/ccgpt/messages",
        json={
            "model": "claude-opus-4-6",
            "max_tokens": 1024,
            "stream": True,
            "messages": [{"role": "user", "content": "way too much context"}],
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "invalid_request_error"
    assert "prompt is too long" in body["error"]["message"].lower()
    assert "message_start" not in json.dumps(body)


@pytest.mark.asyncio
async def test_ccgpt_precontent_top_level_overflow_frame_returns_http_400(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:

    async def fake_stream(request, payload, context, api_key, **kwargs):
        async def source():
            yield 'data: {"type":"response.created","response":{"id":"resp_live","model":"gpt-6.1-sol"}}\n\n'
            yield (
                'data: {"type":"error","status_code":400,"error_type":"invalid_request_error",'
                '"code":"context_length_exceeded",'
                '"message":"Your input exceeds the context window of this model."}\n\n'
            )

        return StreamingResponse(source(), media_type="text/event-stream")

    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream)

    response = await async_client.post(
        "/v1/ccgpt/messages",
        json={
            "model": "claude-opus-4-6",
            "max_tokens": 1024,
            "stream": True,
            "messages": [{"role": "user", "content": "way too much context"}],
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "invalid_request_error"
    assert "prompt is too long" in body["error"]["message"].lower()


@pytest.mark.asyncio
async def test_ccgpt_non_overflow_error_stays_api_error(async_client, monkeypatch: pytest.MonkeyPatch) -> None:
    generic_envelope = {"error": {"type": "server_error", "message": "upstream exploded"}}

    async def fake_stream(request, payload, context, api_key, **kwargs):
        return proxy_api._stream_startup_error_response(
            request,
            ProxyResponseError(500, generic_envelope),
            headers={},
        )

    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream)

    response = await async_client.post(
        "/v1/ccgpt/messages",
        json={
            "model": "claude-opus-4-6",
            "max_tokens": 1024,
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["type"] == "api_error"
    assert body["error"]["message"] == "upstream exploded"
    assert "prompt is too long" not in json.dumps(body).lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_ccgpt_account_rejection_is_retryable_not_a_login_prompt(
    async_client, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:

    async def fake_stream(request, payload, context, api_key, **kwargs):
        return proxy_api._stream_startup_error_response(
            request,
            ProxyResponseError(status, {"error": {"code": "forbidden", "message": "Forbidden"}}),
            headers={},
        )

    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream)

    response = await async_client.post(
        "/v1/ccgpt/messages",
        json={
            "model": "claude-opus-4-6",
            "max_tokens": 1024,
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "api_error"
