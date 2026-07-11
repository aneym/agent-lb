from __future__ import annotations

import pytest
from fastapi.responses import StreamingResponse

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
async def test_ccdex_count_tokens_is_local_and_native(async_client) -> None:
    response = await async_client.post(
        "/v1/ccdex/messages/count_tokens",
        json={"model": "caller-model", "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200
    assert isinstance(response.json()["input_tokens"], int)
    assert response.json()["input_tokens"] > 0
