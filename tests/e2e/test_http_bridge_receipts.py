from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from aiohttp import web

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

_MODEL = "gpt-5.2"
type _Creates = asyncio.Queue[tuple[web.WebSocketResponse, asyncio.Event]]


@asynccontextmanager
async def _bridge_fixture(tmp_path: Path) -> AsyncIterator[tuple[httpx.AsyncClient, _Creates]]:
    creates: _Creates = asyncio.Queue()

    async def responses(request: web.Request) -> web.WebSocketResponse:
        upstream = web.WebSocketResponse()
        await upstream.prepare(request)
        closed = asyncio.Event()
        try:
            async for message in upstream:
                assert message.type == web.WSMsgType.TEXT and message.json()["type"] == "response.create"
                creates.put_nowait((upstream, closed))
        finally:
            closed.set()
        return upstream

    app = web.Application()
    app.router.add_get("/backend-api/codex/responses", responses)
    runner = web.AppRunner(app, shutdown_timeout=1)
    await runner.setup()
    upstream_listener = socket.socket()
    upstream_listener.bind(("127.0.0.1", 0))
    upstream_port = upstream_listener.getsockname()[1]
    await web.SockSite(runner, upstream_listener).start()
    with socket.socket() as port_probe:
        port_probe.bind(("127.0.0.1", 0))
        port = port_probe.getsockname()[1]

    env = {key: value for key, value in os.environ.items() if not key.startswith("AGENT_LB_")}
    env.update(
        AGENT_LB_DATA_DIR=str(tmp_path),
        AGENT_LB_DATABASE_URL=f"sqlite+aiosqlite:///{tmp_path / 'store.db'}",
        AGENT_LB_ENCRYPTION_KEY_FILE=str(tmp_path / "encryption.key"),
        AGENT_LB_UPSTREAM_BASE_URL=f"http://127.0.0.1:{upstream_port}/backend-api",
        AGENT_LB_DASHBOARD_AUTH_MODE="disabled",
        AGENT_LB_DASHBOARD_BOOTSTRAP_TOKEN="synthetic-fixture-bootstrap",
        AGENT_LB_USAGE_REFRESH_ENABLED="false",
        AGENT_LB_MODEL_REGISTRY_ENABLED="false",
        AGENT_LB_ACCOUNT_PULSE_ENABLED="false",
        AGENT_LB_AUTH_GUARDIAN_ENABLED="false",
        AGENT_LB_QUOTA_PLANNER_SCHEDULER_ENABLED="false",
        AGENT_LB_ACCOUNTS_CACHE_WARMER_ENABLED="false",
        AGENT_LB_RESET_CREDIT_AUTO_REDEEM_ENABLED="false",
        AGENT_LB_HTTP_RESPONSES_SESSION_BRIDGE_ENABLED="true",
        AGENT_LB_HTTP_BRIDGE_FIRST_EVENT_TIMEOUT_SECONDS="4",
        AGENT_LB_SSE_KEEPALIVE_INTERVAL_SECONDS="0.1",
        AGENT_LB_CONVERSATION_ARCHIVE_DIR=str(tmp_path / "archive"),
    )
    root = Path(__file__).resolve().parents[2]
    with (tmp_path / "server.log").open("w+") as log:
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=root,
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=12) as client:
                async with asyncio.timeout(30):
                    while True:
                        if server.poll() is not None:
                            log.seek(0)
                            pytest.fail(f"isolated bridge exited: {log.read()}")
                        try:
                            if (await client.get("/health/ready")).status_code == 200:
                                break
                        except httpx.ConnectError:
                            pass
                        await asyncio.sleep(0.1)
                claims = {
                    "email": "bridge-fixture@example.invalid",
                    "exp": int(time.time()) + 3600,
                    "https://api.openai.com/auth": {
                        "chatgpt_account_id": "bridge-fixture",
                        "chatgpt_plan_type": "plus",
                    },
                }
                encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
                auth = {
                    "tokens": {
                        "idToken": f"fixture.{encoded}.signature",
                        "accessToken": f"fixture.{encoded}.signature",
                        "refreshToken": "synthetic-fixture-refresh",
                        "accountId": "bridge-fixture",
                    }
                }
                imported = await client.post(
                    "/api/accounts/import", files={"auth_json": ("auth.json", json.dumps(auth), "application/json")}
                )
                assert imported.status_code == 200, imported.text
                yield client, creates
        finally:
            server.terminate()
            try:
                await asyncio.to_thread(server.wait, timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                await asyncio.to_thread(server.wait)
            await runner.cleanup()


def _post(client: httpx.AsyncClient):
    return client.stream(
        "POST",
        "/backend-api/codex/responses",
        headers={"session_id": "bridge-receipt-fixture", "User-Agent": "codex_cli_rs/0.101.0"},
        json={"model": _MODEL, "input": "Synthetic turn", "stream": True, "store": False},
    )


async def _read_post(client: httpx.AsyncClient) -> str:
    async with _post(client) as response:
        body = (await response.aread()).decode()
        assert response.status_code in {200, 400, 502}, body
        return body


async def _event(upstream: web.WebSocketResponse, response_id: str, status: str) -> None:
    await upstream.send_json(
        {
            "type": "response.created" if status == "in_progress" else "response.completed",
            "response": {"id": response_id, "object": "response", "status": status, "output": []},
        }
    )


async def _receipts(client: httpx.AsyncClient, *codes: str | None) -> dict:
    async with asyncio.timeout(6):
        while True:
            response = await client.get("/api/request-logs", params={"model": _MODEL, "limit": 10})
            assert response.status_code == 200, response.text
            rows = response.json()["requests"]
            if len(rows) >= len(codes):
                break
            await asyncio.sleep(0.05)
    assert len(rows) == len(codes), rows
    assert {row["transport"] for row in rows} == {"http"}
    assert len({row["requestId"] for row in rows}) == len(codes)
    assert {row["errorCode"] for row in rows} == set(codes), rows
    return {row["errorCode"]: row for row in rows}


async def test_first_event_timeout_keeps_its_receipt_and_retires_sibling(tmp_path: Path) -> None:
    async with _bridge_fixture(tmp_path) as (client, creates), asyncio.TaskGroup() as tasks:
        sibling = tasks.create_task(_read_post(client))
        upstream, closed = await asyncio.wait_for(creates.get(), timeout=6)
        await _event(upstream, "resp_sibling", "in_progress")
        primary = tasks.create_task(_read_post(client))
        primary_socket, _ = await asyncio.wait_for(creates.get(), timeout=6)
        assert primary_socket is upstream
        assert "bridge_first_event_timeout" in await primary
        assert "bridge_socket_retired" in await sibling
        await asyncio.wait_for(closed.wait(), timeout=6)
        rows = await _receipts(client, "stream_incomplete", "bridge_socket_retired")
        assert "first-event window" in rows["stream_incomplete"]["errorMessage"]
        assert rows["bridge_socket_retired"]["requestId"] == "resp_sibling"
        assert all(row["upstreamStatusCode"] == 101 and row["upstreamErrorCode"] is None for row in rows.values())


async def test_precreated_disconnect_retires_socket_before_next_post(tmp_path: Path) -> None:
    async with _bridge_fixture(tmp_path) as (client, creates), asyncio.TaskGroup() as tasks:
        async with _post(client) as response:
            assert response.status_code == 200
            upstream, closed = await asyncio.wait_for(creates.get(), timeout=6)
            async for line in response.aiter_lines():
                if line.startswith("event: codex.keepalive"):
                    break
        await asyncio.wait_for(closed.wait(), timeout=3)
        retry = tasks.create_task(_read_post(client))
        fresh_socket, _ = await asyncio.wait_for(creates.get(), timeout=6)
        assert fresh_socket is not upstream
        await _event(fresh_socket, "resp_next_post", "in_progress")
        await _event(fresh_socket, "resp_next_post", "completed")
        assert "resp_next_post" in await retry
        rows = await _receipts(client, "bridge_socket_retired", None)
        assert rows["bridge_socket_retired"]["upstreamErrorCode"] is None


async def test_upstream_error_evidence_stays_with_the_matched_post(tmp_path: Path) -> None:
    async with _bridge_fixture(tmp_path) as (client, creates), asyncio.TaskGroup() as tasks:
        first = tasks.create_task(_read_post(client))
        upstream, _ = await asyncio.wait_for(creates.get(), timeout=6)
        await _event(upstream, "resp_first", "in_progress")
        await upstream.send_json(
            {"type": "response.output_text.delta", "response_id": "resp_first", "delta": "fixture"}
        )
        second = tasks.create_task(_read_post(client))
        second_socket, _ = await asyncio.wait_for(creates.get(), timeout=6)
        assert second_socket is upstream
        await upstream.send_json(
            {
                "type": "error",
                "status": 400,
                "error": {"type": "invalid_request_error", "code": "fixture_validation", "message": "Fixture error"},
            }
        )
        assert "fixture_validation" in await second
        await upstream.close(code=1000)
        assert "stream_incomplete" in await first
        rows = await _receipts(client, "fixture_validation", "stream_incomplete")
        assert rows["fixture_validation"]["upstreamStatusCode"] == 400
        assert rows["fixture_validation"]["upstreamErrorCode"] == "fixture_validation"
        assert rows["stream_incomplete"]["requestId"] == "resp_first"
        assert rows["stream_incomplete"]["upstreamStatusCode"] == 101
        assert rows["stream_incomplete"]["upstreamErrorCode"] is None


async def test_peer_normal_close_before_created_preserves_rejected_input(tmp_path: Path) -> None:
    async with _bridge_fixture(tmp_path) as (client, creates), asyncio.TaskGroup() as tasks:
        post = tasks.create_task(_read_post(client))
        upstream, _ = await asyncio.wait_for(creates.get(), timeout=6)
        await upstream.close(code=1000)
        assert "upstream_rejected_input" in await post
        rows = await _receipts(client, "upstream_rejected_input")
        assert rows["upstream_rejected_input"]["upstreamStatusCode"] == 101
        assert rows["upstream_rejected_input"]["upstreamErrorCode"] is None
