"""Large model-call bodies leave agent-lb gzipped (uplink incident 2026-10-05).

Studio's uplink delivers about 8 Mbps; a 700 KB Messages body took about 15 s
to upload under load. A real local HTTP server stands in for the upstream, so
these tests see the bytes on the wire: they fail if a large body goes out
uncompressed or if the gzipped body decodes to anything but the request.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web

from app.core.clients.proxy import stream_responses
from app.core.clients.upstream_body import UPSTREAM_GZIP_MIN_BYTES
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.openai.requests import ResponsesRequest
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration

ANTHROPIC_SSE = (
    b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","type":"message",'
    b'"role":"assistant","model":"claude-sonnet-5-5","content":[],"usage":{"input_tokens":10}}}\n\n'
    b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
    b'"usage":{"output_tokens":1}}\n\n'
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
)
CODEX_SSE = (
    b'data: {"type":"response.completed","response":{"id":"resp_1","usage":'
    b'{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}\n\n'
)
# Repetitive enough to compress the way real transcripts do, and well over the threshold.
LARGE_TEXT = "tool_result: src/app.py line 42 returned ok\n" * (4 * UPSTREAM_GZIP_MIN_BYTES // 44)


@pytest_asyncio.fixture
async def upstream() -> AsyncIterator[tuple[str, list[dict[str, Any]]]]:
    seen: list[dict[str, Any]] = []

    async def handler(request: web.Request) -> web.StreamResponse:
        # aiohttp inflates gzip request bodies itself; Content-Length is the size on the wire.
        body = await request.read()
        seen.append(
            {
                "path": request.path,
                "encoding": request.headers.get("Content-Encoding"),
                "wire_bytes": int(request.headers["Content-Length"]),
                "json": json.loads(body),
            }
        )
        sse = ANTHROPIC_SSE if request.path.endswith("/v1/messages") else CODEX_SSE
        return web.Response(body=sse, content_type="text/event-stream")

    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.router.add_post("/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    try:
        yield f"http://127.0.0.1:{port}", seen
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_large_anthropic_messages_body_goes_upstream_gzipped(async_client, upstream, monkeypatch):
    base_url, seen = upstream
    monkeypatch.setenv("AGENT_LB_ANTHROPIC_UPSTREAM_BASE_URL", base_url)
    get_settings.cache_clear()
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        session.add(
            Account(
                id="anthropic-gzip",
                provider="anthropic",
                chatgpt_account_id="anthropic-gzip",
                email="claude@example.com",
                plan_type="max",
                access_token_encrypted=encryptor.encrypt("anthropic-access"),
                refresh_token_encrypted=encryptor.encrypt("refresh-anthropic-gzip"),
                id_token_encrypted=None,
                last_refresh=utcnow() + timedelta(days=1),
                status=AccountStatus.ACTIVE,
                deactivation_reason=None,
            )
        )
        await session.commit()

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-5-5",
            "max_tokens": 32,
            "stream": True,
            "messages": [{"role": "user", "content": LARGE_TEXT}],
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )
    get_settings.cache_clear()

    assert response.status_code == 200, response.text
    [call] = seen
    assert call["encoding"] == "gzip"
    assert call["wire_bytes"] < len(LARGE_TEXT) // 4
    assert call["json"]["messages"][0]["content"] == LARGE_TEXT


@pytest.mark.asyncio
async def test_large_codex_responses_body_goes_upstream_gzipped(upstream):
    base_url, seen = upstream
    payload = ResponsesRequest.model_validate(
        {
            "model": "gpt-6.1-sol",
            "instructions": "hi",
            "stream": True,
            "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": LARGE_TEXT}]}],
        }
    )
    async with aiohttp.ClientSession() as session:
        events = [
            event
            async for event in stream_responses(
                payload, {}, "openai-access", "acct-1", base_url=base_url, session=session
            )
        ]

    assert any("response.completed" in event for event in events)
    [call] = seen
    assert call["path"].endswith("/codex/responses")
    assert call["encoding"] == "gzip"
    assert call["wire_bytes"] < len(LARGE_TEXT) // 4
    assert call["json"]["input"][0]["content"][0]["text"] == LARGE_TEXT
