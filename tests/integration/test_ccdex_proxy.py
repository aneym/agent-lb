from __future__ import annotations

import json

import pytest
from fastapi.responses import StreamingResponse

from app.core.clients.proxy import ProxyResponseError
from app.modules.proxy import api as proxy_api
from app.modules.proxy.claude_codex_bridge import CCDEX_MODEL

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_ccdex_messages_uses_openai_responses_path_and_returns_anthropic_sse(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    async def fake_stream(request, payload, context, api_key, **kwargs):
        captured["payload"] = payload
        captured["kwargs"] = kwargs

        async def source():
            yield 'data: {"type":"response.created","response":{"id":"resp_live","model":"gpt-5.6-sol"}}\n\n'
            yield 'data: {"type":"response.output_text.delta","delta":"bridge ok"}\n\n'
            yield 'data: {"type":"response.completed","response":{"usage":{"input_tokens":7,"output_tokens":2}}}\n\n'

        return StreamingResponse(source(), media_type="text/event-stream")

    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/v1/ccdex/messages",
        json={
            "model": "claude-opus-4-6",
            "max_tokens": 1024,
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"authorization": "Bearer claude-secret", "anthropic-beta": "secret-beta"},
    ) as response:
        body = (await response.aread()).decode()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"type":"message_start"' in body
    assert '"type":"text_delta","text":"bridge ok"' in body
    assert '"type":"message_stop"' in body
    translated = captured["payload"]
    assert translated.model == CCDEX_MODEL
    assert translated.reasoning.effort == "high"
    assert translated.service_tier == "priority"
    assert translated.to_payload()["service_tier"] == "priority"
    kwargs = captured["kwargs"]
    assert kwargs["forwarded_headers"].get("authorization") is None
    assert kwargs["forwarded_headers"].get("anthropic-beta") is None
    del kwargs["forwarded_headers"]
    assert kwargs == {
        "codex_session_affinity": True,
        "openai_cache_affinity": True,
        "prefer_http_bridge": True,
        "locked_model": "gpt-5.6-sol",
        "locked_reasoning_effort": "high",
        "locked_service_tier": "priority",
    }


@pytest.mark.asyncio
async def test_ccdex_messages_propagates_per_task_effort(async_client, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_stream(request, payload, context, api_key, **kwargs):
        captured["payload"] = payload
        captured["kwargs"] = kwargs

        async def source():
            yield 'data: {"type":"response.completed","response":{"usage":{}}}\n\n'

        return StreamingResponse(source(), media_type="text/event-stream")

    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream)
    response = await async_client.post(
        "/v1/ccdex/messages",
        json={
            "model": "gpt-5.6-sol",
            "max_tokens": 1024,
            "stream": True,
            "output_config": {"effort": "xhigh"},
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 200
    assert captured["payload"].reasoning.effort == "xhigh"
    assert captured["kwargs"]["locked_reasoning_effort"] == "xhigh"
    assert captured["kwargs"]["locked_service_tier"] == "priority"


@pytest.mark.asyncio
async def test_ccdex_context_overflow_returns_prompt_too_long_not_api_error(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Upstream rejects an over-limit turn with context_length_exceeded before
    # streaming begins; _stream_responses turns that into the real non-streaming
    # error response the ccdex handler must translate. Claude Code only reactive-
    # compacts when the error message contains "prompt is too long" — a bare
    # api_error drives the identical-retry storm this regression guards against.
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
        "/v1/ccdex/messages",
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
async def test_ccdex_midstream_context_overflow_emits_prompt_too_long_without_success(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Upstream begins streaming, then fails mid-stream with a
    # context_length_exceeded envelope on a `response.failed` SSE event. The
    # bridge must translate that into an Anthropic invalid_request_error whose
    # message contains "prompt is too long" (Claude Code's reactive-compaction
    # trigger) and must NOT close the turn with a normal empty success
    # (message_delta/message_stop) after the error.
    async def fake_stream(request, payload, context, api_key, **kwargs):
        async def source():
            yield 'data: {"type":"response.created","response":{"id":"resp_live","model":"gpt-5.6-sol"}}\n\n'
            yield (
                'data: {"type":"response.failed","response":{"error":{'
                '"code":"context_length_exceeded",'
                '"message":"Your input exceeds the context window of this model."}}}\n\n'
            )

        return StreamingResponse(source(), media_type="text/event-stream")

    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/v1/ccdex/messages",
        json={
            "model": "claude-opus-4-6",
            "max_tokens": 1024,
            "stream": True,
            "messages": [{"role": "user", "content": "way too much context"}],
        },
    ) as response:
        body = (await response.aread()).decode()

    assert response.status_code == 200
    assert '"type":"error"' in body
    assert '"type":"invalid_request_error"' in body
    assert "prompt is too long" in body.lower()
    assert "message_stop" not in body
    assert "message_delta" not in body


@pytest.mark.asyncio
async def test_ccdex_non_overflow_error_stays_api_error(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    generic_envelope = {"error": {"type": "server_error", "message": "upstream exploded"}}

    async def fake_stream(request, payload, context, api_key, **kwargs):
        return proxy_api._stream_startup_error_response(
            request,
            ProxyResponseError(500, generic_envelope),
            headers={},
        )

    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream)

    response = await async_client.post(
        "/v1/ccdex/messages",
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
async def test_ccdex_count_tokens_is_local_and_native(async_client) -> None:
    response = await async_client.post(
        "/v1/ccdex/messages/count_tokens",
        json={"model": "caller-model", "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200
    assert isinstance(response.json()["input_tokens"], int)
    assert response.json()["input_tokens"] > 0
