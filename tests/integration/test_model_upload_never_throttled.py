"""Incident 2026-10-05: an upload hold must never delay or refuse a model request.

`game-mode on` set a 0.2 MB/s agent-lb upload hold, and Claude Code calls failed with
"API Error: 503 upload throttle on at 200 KB/s; 3.4 MB queued ahead; waited 30 s > limit 30 s".
Holds are for bulk transfers only. This drives /v1/messages through the real proxy route, the
real aiohttp upstream client and the installed upload pacer, against a local stand-in for the
Anthropic API reached by a non-loopback host name, with the slowest accepted hold on.
Only the network edge is faked: name resolution and the upstream server.
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
from datetime import timedelta
from pathlib import Path

import pytest
from aiohttp import web
from sqlalchemy import select

from app.core import upload_throttle
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, RequestLog
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration

UPSTREAM_HOST = "model-upstream.test"
HOLD_BYTES_PER_SEC = 65_000  # the slowest hold `agent-lb throttle on` accepts
BODY_BYTES = 400_000
REQUESTS = 2
# Paced at the hold, the bodies alone need about 12 s; unpaced over loopback they need well under 1 s.
PACED_SECONDS = REQUESTS * BODY_BYTES / HOLD_BYTES_PER_SEC
NOT_DELAYED_SECONDS = PACED_SECONDS / 3

SSE = (
    b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","type":"message",'
    b'"role":"assistant","model":"claude-opus-5-5","content":[],"usage":{"input_tokens":10,'
    b'"cache_creation_input_tokens":0,"cache_read_input_tokens":0}}}\n\n'
    b'event: content_block_start\ndata: {"type":"content_block_start","index":0,'
    b'"content_block":{"type":"text","text":""}}\n\n'
    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
    b'"delta":{"type":"text_delta","text":"ok"}}\n\n'
    b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
    b'"usage":{"output_tokens":1}}\n\n'
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
)


@pytest.fixture
def game_mode_hold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_LB_THROTTLE_FILE", str(tmp_path / "upload-throttle.json"))
    upload_throttle.write_state(enabled=True, owner="game-mode", bytes_per_sec=HOLD_BYTES_PER_SEC, reason="gaming")
    monkeypatch.setattr(upload_throttle, "_BUCKET", None)
    assert upload_throttle.read_state() == (True, float(HOLD_BYTES_PER_SEC))


@pytest.fixture
async def fake_anthropic(monkeypatch: pytest.MonkeyPatch):
    received: list[int] = []

    async def messages(request: web.Request) -> web.StreamResponse:
        received.append(len(await request.read()))
        response = web.StreamResponse(status=200, headers={"content-type": "text/event-stream"})
        await response.prepare(request)
        await response.write(SSE)
        await response.write_eof()
        return response

    app = web.Application(client_max_size=10_000_000)
    app.router.add_post("/v1/messages", messages)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

    real_getaddrinfo = socket.getaddrinfo

    def resolve(host, *args, **kwargs):
        return real_getaddrinfo("127.0.0.1" if host == UPSTREAM_HOST else host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setenv("AGENT_LB_ANTHROPIC_UPSTREAM_BASE_URL", f"http://{UPSTREAM_HOST}:{port}")
    get_settings.cache_clear()
    try:
        yield received
    finally:
        await runner.cleanup()


async def _insert_anthropic_account() -> None:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        session.add(
            Account(
                id="anthropic-gaming",
                provider="anthropic",
                chatgpt_account_id="anthropic-gaming",
                email="gaming@example.com",
                plan_type="max",
                access_token_encrypted=encryptor.encrypt("anthropic-access"),
                refresh_token_encrypted=encryptor.encrypt("refresh-anthropic-gaming"),
                id_token_encrypted=None,
                last_refresh=utcnow() + timedelta(days=1),
                status=AccountStatus.ACTIVE,
                deactivation_reason=None,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_a_game_mode_hold_never_delays_or_refuses_a_model_request(game_mode_hold, fake_anthropic, async_client):
    await _insert_anthropic_account()

    async def call(index: int) -> tuple[int, bytes]:
        payload = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 32,
            "stream": True,
            "messages": [{"role": "user", "content": f"{index}" + "x" * BODY_BYTES}],
        }
        async with async_client.stream(
            "POST",
            "/v1/messages",
            content=json.dumps(payload),
            headers={
                "content-type": "application/json",
                "anthropic-beta": "oauth-2025-04-20",
                "user-agent": "claude-cli/2.1.289 (external, cli)",
                "x-claude-code-session-id": f"gaming-{index}",
            },
            timeout=60,
        ) as response:
            return response.status_code, await response.aread()

    started = time.monotonic()
    results = await asyncio.gather(*(call(index) for index in range(REQUESTS)))
    elapsed = time.monotonic() - started

    assert [status for status, _ in results] == [200] * REQUESTS, results
    assert elapsed < NOT_DELAYED_SECONDS, f"model uploads took {elapsed:.1f} s under a hold"
    for index, (_, body) in enumerate(results):
        assert body == SSE, f"request {index} got {body[:240]!r}"
    assert len(fake_anthropic) == REQUESTS and min(fake_anthropic) > BODY_BYTES
    async with SessionLocal() as session:
        logs = list((await session.execute(select(RequestLog))).scalars())
    assert sorted(log.status for log in logs) == ["success"] * REQUESTS
    assert not [log.error_code for log in logs if log.error_code]
