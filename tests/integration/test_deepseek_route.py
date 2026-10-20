from __future__ import annotations

import json

import aiohttp
import pytest
from curl_cffi.requests import AsyncSession
from sqlalchemy import select

from app.db.models import RequestLog
from app.db.session import SessionLocal
from tests.integration.anthropic_proxy_support import _FakeResponse, _FakeResponseContext, _insert_account

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_deepseek_openai_transport(async_client, monkeypatch):
    await _insert_account(
        account_id="router", provider="openrouter", access_token="test-router", email="r@example.test"
    )
    await _insert_account(account_id="codex", provider="openai", access_token="test-codex", email="c@example.test")
    calls = []
    chunk = {
        "id": "reply",
        "object": "chat.completion.chunk",
        "model": "deepseek/deepseek-v4.1-flash-1017",
        "choices": [
            {
                "index": 0,
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_original",
                            "type": "function",
                            "function": {"name": "inspect", "arguments": '{"x":1}'},
                        }
                    ]
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 4, "cost": 0.00123},
    }
    wire = ("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode()

    def post(session, url, **kwargs):
        calls.append((str(url), kwargs))
        if "openrouter.ai" in str(url):
            if str(url).endswith("/messages"):
                return _FakeResponseContext(
                    _FakeResponse(
                        200,
                        b'{"id":"msg","type":"message","role":"assistant","content":[],"usage":{"input_tokens":2,"output_tokens":1}}',
                    )
                )
            if kwargs["json"].get("stream"):
                return _FakeResponseContext(_FakeResponse(200, wire))
            return _FakeResponseContext(_FakeResponse(200, json.dumps(chunk).encode()))
        body = b'data: {"type":"response.completed","response":{"id":"codex","status":"completed","output":[],"usage":{"input_tokens":1,"output_tokens":1}}}\n\n'
        return _FakeResponseContext(_FakeResponse(200, body, {"content-type": "text/event-stream"}))

    class CodexReply:
        status_code = 200

        async def aiter_content(self):
            yield b'data: {"type":"response.completed","response":{"id":"codex","status":"completed","output":[],"usage":{"input_tokens":1,"output_tokens":1}}}\n\n'

        async def aclose(self):
            pass

    async def codex_request(session, method, url, **kwargs):
        calls.append((str(url), kwargs))
        return CodexReply()

    monkeypatch.setattr(AsyncSession, "request", codex_request)

    class NetworkReply(_FakeResponse):
        async def text(self):
            return (await self.read()).decode()

        async def json(self, **kwargs):
            return json.loads(await self.read())

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            pass

        def release(self):
            pass

        async def wait_for_close(self):
            pass

    async def network_request(session, method, url, **kwargs):
        response = post(session, url, **kwargs)._response
        return NetworkReply(response.status, await response.read(), response.headers)

    monkeypatch.setattr(aiohttp.ClientSession, "_request", network_request)
    monkeypatch.setattr(aiohttp.ClientSession, "post", post)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "inspect"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ],
        }
    ]
    tools = [{"type": "function", "function": {"name": "inspect", "parameters": {"type": "object"}}}]
    response = await async_client.post(
        "/v1/chat/completions",
        json={
            "model": "deepseek-flash",
            "messages": messages,
            "tools": tools,
            "tool_choice": "required",
            "stream": True,
        },
    )
    assert response.status_code == 200, response.text
    assert response.content == wire
    url, kwargs = calls[-1]
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert kwargs["headers"]["Authorization"] == "Bearer test-router"
    assert kwargs["json"]["model"] == "deepseek/deepseek-v4.1-flash-1017"
    assert kwargs["json"]["provider"] == {
        "order": ["inference-net/fp8", "deepinfra/fp8/priority", "morph/fp8", "cloudflare"],
        "allow_fallbacks": False,
    }
    assert kwargs["json"]["messages"] == messages
    assert kwargs["json"]["tools"] == tools
    assert kwargs["json"]["tool_choice"] == "required"
    async with SessionLocal() as session:
        log = (await session.execute(select(RequestLog).where(RequestLog.account_id == "router"))).scalar_one()
        assert (log.provider, log.input_tokens, log.output_tokens, log.cost_usd) == ("openrouter", 12, 4, 0.00123)
    response = await async_client.post(
        "/v1/chat/completions", json={"model": "deepseek-flash-floor", "messages": messages}
    )
    assert response.status_code == 200, response.text
    assert calls[-1][1]["json"]["model"] == "deepseek/deepseek-v4.1-flash-1017:floor"
    assert "provider" not in calls[-1][1]["json"]
    response = await async_client.post(
        "/v1/messages",
        json={"model": "deepseek-flash", "max_tokens": 20, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200, response.text
    assert calls[-1][0] == "https://openrouter.ai/api/v1/messages"
    assert calls[-1][1]["json"]["provider"]["order"][0] == "inference-net/fp8"
    response = await async_client.get("/v1/models")
    assert "deepseek-flash" in [model["id"] for model in response.json()["data"]]
    response = await async_client.post(
        "/v1/chat/completions", json={"model": "gpt-5.2", "messages": [{"role": "user", "content": "hi"}]}
    )
    async with SessionLocal() as session:
        errors = list(
            (await session.execute(select(RequestLog.error_message).where(RequestLog.account_id == "codex"))).scalars()
        )
    assert response.status_code == 200, errors
    assert calls[-1][0].endswith("/responses")
    assert "openrouter" not in calls[-1][0]
    assert calls[-1][1]["headers"]["Authorization"] == "Bearer test-codex"
