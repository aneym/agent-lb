"""Large model-call bodies leave agent-lb gzipped (uplink incident 2026-10-05).

Studio's uplink delivers about 8 Mbps; a 700 KB Messages body took about 15 s
to upload under load. A real local HTTP server stands in for the upstream, so
these tests see the bytes on the wire: they fail if a large body goes out
uncompressed or if the gzipped body decodes to anything but the request.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web
from sqlalchemy import select

from app.core.clients import upstream_body
from app.core.clients.proxy import pop_stream_timeout_overrides, push_stream_timeout_overrides, stream_responses
from app.core.clients.upstream_body import UPSTREAM_GZIP_MIN_BYTES
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.openai.requests import ResponsesRequest
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, ApiKeyUsageReservation
from app.db.session import SessionLocal
from app.modules.proxy import anthropic_service

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

    async with _serve(handler) as base_url:
        yield base_url, seen


@asynccontextmanager
async def _serve(handler: Callable[[web.Request], Awaitable[web.StreamResponse]]) -> AsyncIterator[str]:
    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.router.add_post("/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()


async def _insert_anthropic_account(account_id: str = "anthropic-gzip") -> None:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        session.add(
            Account(
                id=account_id,
                provider="anthropic",
                chatgpt_account_id=account_id,
                email=f"{account_id}@example.com",
                plan_type="max",
                access_token_encrypted=encryptor.encrypt(f"access-{account_id}"),
                refresh_token_encrypted=encryptor.encrypt(f"refresh-{account_id}"),
                id_token_encrypted=None,
                last_refresh=utcnow() + timedelta(days=1),
                status=AccountStatus.ACTIVE,
                deactivation_reason=None,
            )
        )
        await session.commit()


def _occupy_gzip_workers(release: threading.Event) -> list[asyncio.Future[bool]]:
    loop = asyncio.get_running_loop()
    return [
        loop.run_in_executor(upstream_body._GZIP_EXECUTOR, release.wait, 5.0)
        for _ in range(upstream_body._GZIP_EXECUTOR._max_workers)
    ]


@asynccontextmanager
async def _gzip_workers_busy() -> AsyncIterator[None]:
    release = threading.Event()
    blockers = _occupy_gzip_workers(release)
    try:
        yield
    finally:
        release.set()
        await asyncio.gather(*blockers)


@pytest.mark.asyncio
async def test_large_anthropic_messages_body_goes_upstream_gzipped(async_client, upstream, monkeypatch):
    base_url, seen = upstream
    monkeypatch.setenv("AGENT_LB_ANTHROPIC_UPSTREAM_BASE_URL", base_url)
    get_settings.cache_clear()
    await _insert_anthropic_account()

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


def _large_codex_payload() -> ResponsesRequest:
    return ResponsesRequest.model_validate(
        {
            "model": "gpt-6.1-sol",
            "instructions": "hi",
            "stream": True,
            "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": LARGE_TEXT}]}],
        }
    )


async def _stream_codex(base_url: str) -> list[str]:
    async with aiohttp.ClientSession() as session:
        return [
            event
            async for event in stream_responses(
                _large_codex_payload(), {}, "openai-access", "acct-1", base_url=base_url, session=session
            )
        ]


@pytest.mark.asyncio
async def test_large_codex_responses_body_goes_upstream_gzipped(upstream):
    base_url, seen = upstream
    events = await _stream_codex(base_url)

    assert any("response.completed" in event for event in events)
    [call] = seen
    assert call["path"].endswith("/codex/responses")
    assert call["encoding"] == "gzip"
    assert call["wire_bytes"] < len(LARGE_TEXT) // 4
    assert call["json"]["input"][0]["content"][0]["text"] == LARGE_TEXT


@pytest.mark.asyncio
async def test_gzip_off_switch_sends_large_body_uncompressed(upstream, monkeypatch):
    base_url, seen = upstream
    monkeypatch.setenv("AGENT_LB_UPSTREAM_REQUEST_GZIP_ENABLED", "false")
    get_settings.cache_clear()
    try:
        events = await _stream_codex(base_url)
    finally:
        monkeypatch.delenv("AGENT_LB_UPSTREAM_REQUEST_GZIP_ENABLED")
        get_settings.cache_clear()

    assert any("response.completed" in event for event in events)
    [call] = seen
    assert call["encoding"] is None
    assert call["json"]["input"][0]["content"][0]["text"] == LARGE_TEXT


@pytest.mark.asyncio
async def test_budget_spent_waiting_for_compression_fails_before_upload(upstream):
    """Release-review settling check: with every gzip worker busy, a request whose
    budget runs out fails at its deadline and never reaches the upstream."""
    base_url, seen = upstream
    tokens = push_stream_timeout_overrides(total_timeout_seconds=0.05)
    started = time.monotonic()
    try:
        async with _gzip_workers_busy():
            events = await _stream_codex(base_url)
            elapsed = time.monotonic() - started
    finally:
        pop_stream_timeout_overrides(tokens)

    assert elapsed < 1.0
    assert any("upstream_request_timeout" in event for event in events)
    assert seen == []


@pytest.mark.asyncio
async def test_anthropic_budget_spent_waiting_for_compression_is_a_timeout_and_releases_the_reservation(
    async_client, upstream, monkeypatch
):
    """Post-merge review of 9321d954: on the Anthropic paths the deadline used to
    escape as a bare 500 and leave the API-key reservation reserved."""
    base_url, seen = upstream
    monkeypatch.setenv("AGENT_LB_ANTHROPIC_UPSTREAM_BASE_URL", base_url)
    monkeypatch.setenv("AGENT_LB_PROXY_REQUEST_BUDGET_SECONDS", "0.05")
    monkeypatch.setattr(anthropic_service, "_COUNT_TOKENS_TIMEOUT_SECONDS", 0.05)
    get_settings.cache_clear()
    await _insert_anthropic_account()
    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "gzip-deadline",
            "limits": [{"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 10_000_000}],
        },
    )
    assert created.status_code == 200
    key, key_id = created.json()["key"], created.json()["id"]
    request = {
        "model": "claude-sonnet-5-5",
        "max_tokens": 32,
        "messages": [{"role": "user", "content": LARGE_TEXT}],
    }
    headers = {"Authorization": f"Bearer {key}", "anthropic-beta": "oauth-2025-04-20"}

    try:
        async with _gzip_workers_busy():
            message = await async_client.post("/v1/messages", json=request, headers=headers)
            count = await async_client.post("/v1/messages/count_tokens", json=request, headers=headers)
    finally:
        get_settings.cache_clear()

    for response in (message, count):
        assert response.status_code == 504, response.text
        assert response.json()["error"]["type"] == "upstream_request_timeout"
    assert seen == []
    async with SessionLocal() as session:
        reservations = (
            (await session.execute(select(ApiKeyUsageReservation).where(ApiKeyUsageReservation.api_key_id == key_id)))
            .scalars()
            .all()
        )
    assert [reservation.status for reservation in reservations] == ["released"]


@pytest.mark.asyncio
async def test_a_compression_deadline_after_an_overloaded_account_stays_a_timeout(async_client, monkeypatch):
    """Post-merge review of 4abb84af: the first account answers 529, the gzip workers
    then stay busy past the second attempt's budget. The 504 timeout used to be
    rewritten as 529 overloaded_error by the after-529s branch."""
    calls: list[str] = []
    release = threading.Event()
    blockers: list[asyncio.Future[bool]] = []

    async def overloaded_then_busy(request: web.Request) -> web.StreamResponse:
        calls.append(request.headers["Authorization"])
        blockers.extend(_occupy_gzip_workers(release))
        return web.json_response(
            {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}, status=529
        )

    await _insert_anthropic_account("anthropic-gzip-a")
    await _insert_anthropic_account("anthropic-gzip-b")
    try:
        async with _serve(overloaded_then_busy) as base_url:
            monkeypatch.setenv("AGENT_LB_ANTHROPIC_UPSTREAM_BASE_URL", base_url)
            monkeypatch.setenv("AGENT_LB_PROXY_REQUEST_BUDGET_SECONDS", "1")
            get_settings.cache_clear()
            response = await async_client.post(
                "/v1/messages",
                json={
                    "model": "claude-sonnet-5-5",
                    "max_tokens": 32,
                    "messages": [{"role": "user", "content": LARGE_TEXT}],
                },
                headers={"anthropic-beta": "oauth-2025-04-20"},
            )
    finally:
        get_settings.cache_clear()
        release.set()
        await asyncio.gather(*blockers)

    assert len(calls) == 1
    assert response.status_code == 504, response.text
    assert response.json()["error"]["type"] == "upstream_request_timeout"
