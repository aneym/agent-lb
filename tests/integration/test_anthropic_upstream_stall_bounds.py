"""Incident 2026-10-05: a streamed Claude call must not wait minutes on a silent upstream.

Two Claude Code calls from one tab waited 9 and 13 minutes: request f7612404 got its first
byte after 553.6 s, and agent-lb had no bound short of its 600 s total budget. These drive
/v1/messages through the real route, account loop and aiohttp upstream client against a
local stand-in for the Anthropic API. Only the network edge is faked: name resolution and
the upstream server, which goes silent where the incident did.
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
from datetime import timedelta, timezone

import pytest
from aiohttp import web
from sqlalchemy import select

from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, AdditionalUsageHistory, RequestLog
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration

UPSTREAM_HOST = "model-upstream.test"
QUOTA_KEY = "anthropic_top"  # the quota key of the model these calls use
BOUND_SECONDS = 0.5
STALL_SECONDS = 8.0  # how long the stand-in stays silent; the old code waits all of it
BOUNDED_SECONDS = 4.0

MESSAGE_START = (
    b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","type":"message",'
    b'"role":"assistant","model":"claude-opus-5-5","content":[],"usage":{"input_tokens":10,'
    b'"cache_creation_input_tokens":0,"cache_read_input_tokens":0}}}\n\n'
)
REST = (
    b'event: content_block_start\ndata: {"type":"content_block_start","index":0,'
    b'"content_block":{"type":"text","text":""}}\n\n'
    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
    b'"delta":{"type":"text_delta","text":"ok"}}\n\n'
    b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
    b'"usage":{"output_tokens":1}}\n\n'
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
)
SSE = MESSAGE_START + REST
CUT = REST.index(b'"text":"') + 4  # inside an event's JSON
JSON_MESSAGE = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-opus-5-5",
    "content": [{"type": "text", "text": "ok"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 10, "output_tokens": 1},
}


class StandIn:
    """Anthropic stand-in. ``mode`` picks how the first call misbehaves; later calls answer."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls = 0
        self.release = asyncio.Event()

    async def _silence(self) -> None:
        try:
            await asyncio.wait_for(self.release.wait(), STALL_SECONDS)
        except TimeoutError:
            pass

    async def messages(self, request: web.Request) -> web.StreamResponse:
        body = await request.json()
        self.calls += 1
        first = self.calls == 1
        if not body.get("stream"):
            await asyncio.sleep(2 * BOUND_SECONDS)  # a reply that is slow but not stalled
            return web.json_response(JSON_MESSAGE)
        if first and self.mode == "silent_before_headers":
            await self._silence()
        if first and self.mode == "overloaded_status_silent_body":
            response = web.StreamResponse(status=529, headers={"content-type": "application/json"})
            await response.prepare(request)
            await self._silence()
            await response.write(b'{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}')
            await response.write_eof()
            return response
        response = web.StreamResponse(status=200, headers={"content-type": "text/event-stream"})
        await response.prepare(request)
        if first and self.mode == "silent_mid_event":
            await response.write(MESSAGE_START + REST[:CUT])
            await self._silence()
            await response.write(REST[CUT:])
        else:
            await response.write(SSE)
        await response.write_eof()
        return response


async def _serve(stand_in: StandIn, monkeypatch: pytest.MonkeyPatch) -> web.AppRunner:
    app = web.Application(client_max_size=10_000_000)
    app.router.add_post("/v1/messages", stand_in.messages)
    runner = web.AppRunner(app, shutdown_timeout=1)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    real_getaddrinfo = socket.getaddrinfo

    def resolve(host, *args, **kwargs):
        return real_getaddrinfo("127.0.0.1" if host == UPSTREAM_HOST else host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setenv("AGENT_LB_ANTHROPIC_UPSTREAM_BASE_URL", f"http://{UPSTREAM_HOST}:{port}")
    monkeypatch.setenv("AGENT_LB_ANTHROPIC_FIRST_BYTE_TIMEOUT_SECONDS", str(BOUND_SECONDS))
    monkeypatch.setenv("AGENT_LB_ANTHROPIC_STREAM_IDLE_TIMEOUT_SECONDS", str(BOUND_SECONDS))
    get_settings.cache_clear()
    return runner


@pytest.fixture
async def upstream(request, monkeypatch: pytest.MonkeyPatch):
    stand_in = StandIn(request.param)
    runner = await _serve(stand_in, monkeypatch)
    try:
        yield stand_in
    finally:
        stand_in.release.set()
        await runner.cleanup()
        get_settings.cache_clear()


async def _insert_anthropic_accounts(count: int, *, cooling: int = 0) -> None:
    """Insert ``count`` active accounts; the last ``cooling`` of them sit in a quota cooldown."""
    encryptor = TokenEncryptor()
    reset_at = int((utcnow() + timedelta(minutes=7)).replace(tzinfo=timezone.utc).timestamp())
    async with SessionLocal() as session:
        for index in range(count):
            account_id = f"anthropic-stall-{index}"
            session.add(
                Account(
                    id=account_id,
                    provider="anthropic",
                    chatgpt_account_id=account_id,
                    email=f"stall-{index}@example.com",
                    plan_type="max",
                    access_token_encrypted=encryptor.encrypt(f"anthropic-access-{index}"),
                    refresh_token_encrypted=encryptor.encrypt(f"refresh-{account_id}"),
                    id_token_encrypted=None,
                    last_refresh=utcnow() + timedelta(days=1),
                    status=AccountStatus.ACTIVE,
                    deactivation_reason=None,
                )
            )
            if index >= count - cooling:
                session.add(
                    AdditionalUsageHistory(
                        account_id=account_id,
                        quota_key=QUOTA_KEY,
                        limit_name=QUOTA_KEY,
                        metered_feature="anthropic_messages",
                        window="primary",
                        used_percent=100.0,
                        reset_at=reset_at,
                        window_minutes=10,
                        recorded_at=utcnow(),
                    )
                )
        await session.commit()


async def _call(async_client, *, stream: bool, headers: dict[str, str] | None = None) -> tuple[int, bytes, float]:
    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 32,
        "stream": stream,
        "messages": [{"role": "user", "content": "hello"}],
    }
    started = time.monotonic()
    async with async_client.stream(
        "POST",
        "/v1/messages",
        content=json.dumps(payload),
        headers={
            "content-type": "application/json",
            "anthropic-beta": "oauth-2025-04-20",
            "user-agent": "claude-cli/2.1.289 (external, cli)",
            "x-claude-code-session-id": "stall-tab",
            **(headers or {}),
        },
        timeout=60,
    ) as response:
        body = await response.aread()
    return response.status_code, body, time.monotonic() - started


async def _logs() -> list[RequestLog]:
    async with SessionLocal() as session:
        return list((await session.execute(select(RequestLog).order_by(RequestLog.requested_at))).scalars())


@pytest.mark.asyncio
@pytest.mark.parametrize("upstream", ["silent_before_headers"], indirect=True)
async def test_a_silent_upstream_moves_the_call_to_another_account_within_the_bound(upstream, async_client):
    await _insert_anthropic_accounts(2)

    status, body, elapsed = await _call(async_client, stream=True)

    assert elapsed < BOUNDED_SECONDS, f"the call waited {elapsed:.1f} s on a silent upstream"
    assert status == 200
    assert body == SSE, body[:240]
    logs = await _logs()
    assert [(log.status, log.error_code) for log in logs] == [
        ("error", "upstream_first_byte_timeout"),
        ("success", None),
    ]
    assert logs[0].account_id != logs[1].account_id


@pytest.mark.asyncio
@pytest.mark.parametrize("upstream", ["overloaded_status_silent_body"], indirect=True)
async def test_an_error_status_with_a_silent_body_also_moves_to_another_account(upstream, async_client):
    await _insert_anthropic_accounts(2)

    status, body, elapsed = await _call(async_client, stream=True)

    assert elapsed < BOUNDED_SECONDS, f"the call waited {elapsed:.1f} s on a silent error body"
    assert status == 200
    assert body == SSE, body[:240]
    assert [(log.status, log.error_code) for log in await _logs()] == [
        ("error", "upstream_first_byte_timeout"),
        ("success", None),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("upstream", ["silent_mid_event"], indirect=True)
async def test_a_stream_that_goes_silent_mid_event_ends_with_a_clean_retryable_error_event(upstream, async_client):
    await _insert_anthropic_accounts(1)

    status, body, elapsed = await _call(async_client, stream=True)

    assert elapsed < BOUNDED_SECONDS, f"the stream hung {elapsed:.1f} s after going silent"
    assert status == 200
    assert body.startswith(MESSAGE_START)
    # The half-sent event never reaches the client; every event it gets parses.
    events = [block for block in body[len(MESSAGE_START) :].split(b"\n\n") if block.strip()]
    assert len(events) == 1 and events[0].startswith(b"event: error\ndata: "), body
    error = json.loads(events[0].split(b"data: ", 1)[1])
    assert error["error"]["type"] == "overloaded_error"
    assert [(log.status, log.error_code) for log in await _logs()] == [("error", "upstream_stream_idle_timeout")]


@pytest.mark.asyncio
@pytest.mark.parametrize("upstream", ["silent_before_headers"], indirect=True)
async def test_running_out_of_accounts_after_a_stall_is_an_overload_not_a_quota_wait(
    upstream, async_client, monkeypatch
):
    # The only other account is in a quota cooldown with a reset ahead.
    monkeypatch.setenv("AGENT_LB_ANTHROPIC_POOL_EXHAUSTED_WAIT_ENABLED", "false")
    get_settings.cache_clear()
    await _insert_anthropic_accounts(2, cooling=1)

    status, body, elapsed = await _call(async_client, stream=True, headers={"x-claude-code-agent-id": "sub-1"})

    assert elapsed < BOUNDED_SECONDS, f"the call waited {elapsed:.1f} s"
    if body.startswith(b"event: error"):
        error = json.loads(body.split(b"data: ", 1)[1])
    else:
        error = json.loads(body)
    assert error["error"]["type"] == "overloaded_error", (status, body[:300])
    assert upstream.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("upstream", ["answers"], indirect=True)
async def test_a_slow_non_streamed_reply_is_not_cut_by_the_stream_bounds(upstream, async_client):
    await _insert_anthropic_accounts(1)

    status, body, _ = await _call(async_client, stream=False)

    assert status == 200
    assert json.loads(body)["content"] == [{"type": "text", "text": "ok"}]
    assert [(log.status, log.error_code) for log in await _logs()] == [("success", None)]
