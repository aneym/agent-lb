from __future__ import annotations

import pytest
from fastapi.responses import StreamingResponse

from app.modules.proxy import api as proxy_api
from app.modules.proxy.claude_codex_bridge import CCGPT_MODEL

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_ccgpt_messages_uses_openai_responses_path_and_returns_anthropic_sse(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    async def fake_stream(request, payload, context, api_key, **kwargs):
        captured["payload"] = payload
        captured["kwargs"] = kwargs

        async def source():
            yield 'data: {"type":"response.created","response":{"id":"resp_live","model":"gpt-6.1-sol"}}\n\n'
            yield 'data: {"type":"response.output_text.delta","delta":"bridge ok"}\n\n'
            yield 'data: {"type":"response.completed","response":{"usage":{"input_tokens":7,"output_tokens":2}}}\n\n'

        return StreamingResponse(source(), media_type="text/event-stream")

    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/v1/ccgpt/messages",
        json={
            "model": "claude-opus-4-6",
            "max_tokens": 1024,
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={
            "authorization": "Bearer claude-secret",
            "anthropic-beta": "secret-beta",
            "X-Claude-Code-Session-Id": "coordinator-session",
        },
    ) as response:
        body = (await response.aread()).decode()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"type":"message_start"' in body
    assert '"type":"text_delta","text":"bridge ok"' in body
    assert '"type":"message_stop"' in body
    translated = captured["payload"]
    assert translated.model == CCGPT_MODEL
    assert translated.reasoning.effort == "high"
    assert translated.service_tier == "priority"
    assert translated.to_payload()["service_tier"] == "priority"
    kwargs = captured["kwargs"]
    assert kwargs["forwarded_headers"].get("authorization") is None
    assert kwargs["forwarded_headers"].get("anthropic-beta") is None
    assert kwargs["forwarded_headers"]["x-claude-code-session-id"] == "coordinator-session"
    del kwargs["forwarded_headers"]
    assert kwargs == {
        "client_session_id": "coordinator-session",
        "codex_session_affinity": True,
        "openai_cache_affinity": True,
        "prefer_http_bridge": True,
        "locked_model": "gpt-6.1-sol",
        "locked_reasoning_effort": "high",
        "locked_service_tier": "priority",
    }


@pytest.mark.asyncio
async def test_ccgpt_messages_propagates_per_task_effort(async_client, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_stream(request, payload, context, api_key, **kwargs):
        captured["payload"] = payload
        captured["kwargs"] = kwargs

        async def source():
            yield 'data: {"type":"response.completed","response":{"usage":{}}}\n\n'

        return StreamingResponse(source(), media_type="text/event-stream")

    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream)
    response = await async_client.post(
        "/v1/ccgpt/messages",
        json={
            "model": "gpt-6.1-sol",
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
    assert captured["kwargs"]["client_session_id"] is None
