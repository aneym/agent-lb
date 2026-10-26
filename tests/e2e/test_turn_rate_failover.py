from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from aiohttp import web


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("capacity", [False, True, "message_only"])
async def test_turn_rate_limit_fails_over_http_bridge(tmp_path, capacity):
    attempts: list[str] = []

    async def responses(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for message in ws:
            if message.type != web.WSMsgType.TEXT:
                continue
            account = request.headers["ChatGPT-Account-ID"]
            attempts.append(account)
            if len(attempts) == 1:
                await ws.send_json(
                    {
                        "type": "error",
                        "status": 429,
                        "error": {
                            "type": "invalid_request_error" if capacity else "rate_limit_error",
                            "code": (
                                None
                                if capacity == "message_only"
                                else "model_capacity_exceeded"
                                if capacity
                                else "turn_rate_limit_exceeded"
                            ),
                            "message": (
                                "Selected model is at capacity. Please try a different model."
                                if capacity
                                else "Codex is at capacity"
                            ),
                        },
                    }
                )
            else:
                await ws.send_json(
                    {
                        "type": "response.created",
                        "response": {
                            "id": "resp_failover",
                            "object": "response",
                            "status": "in_progress",
                            "output": [],
                        },
                    }
                )
                await ws.send_json(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_failover",
                            "object": "response",
                            "status": "completed",
                            "output": [],
                            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                        },
                    }
                )
        return ws

    upstream = web.Application()
    upstream.router.add_get("/backend-api/codex/responses", responses)
    runner = web.AppRunner(upstream)
    await runner.setup()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    upstream_port = listener.getsockname()[1]
    await web.SockSite(runner, listener).start()
    with socket.socket() as port_probe:
        port_probe.bind(("127.0.0.1", 0))
        port = port_probe.getsockname()[1]
    env = {key: value for key, value in os.environ.items() if not key.startswith("AGENT_LB_")}
    env.update(
        {
            "AGENT_LB_DATA_DIR": str(tmp_path),
            "AGENT_LB_DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'store.db'}",
            "AGENT_LB_ENCRYPTION_KEY_FILE": str(tmp_path / "encryption.key"),
            "AGENT_LB_UPSTREAM_BASE_URL": f"http://127.0.0.1:{upstream_port}/backend-api",
            "AGENT_LB_DASHBOARD_AUTH_MODE": "disabled",
            "AGENT_LB_USAGE_REFRESH_ENABLED": "false",
            "AGENT_LB_MODEL_REGISTRY_ENABLED": "false",
            "AGENT_LB_ACCOUNT_PULSE_ENABLED": "false",
            "AGENT_LB_AUTH_GUARDIAN_ENABLED": "false",
            "AGENT_LB_QUOTA_PLANNER_SCHEDULER_ENABLED": "false",
            "AGENT_LB_ACCOUNTS_CACHE_WARMER_ENABLED": "false",
            "AGENT_LB_RESET_CREDIT_AUTO_REDEEM_ENABLED": "false",
            "AGENT_LB_HTTP_RESPONSES_SESSION_BRIDGE_ENABLED": "true",
            "AGENT_LB_CONVERSATION_ARCHIVE_DIR": str(tmp_path / "archive"),
        }
    )
    seed = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "from sqlalchemy import create_engine; from app.db.base import Base; import app.db.models; "
        "from alembic.config import Config; from alembic import command; "
        "import os; url=os.environ['AGENT_LB_DATABASE_URL'].replace('+aiosqlite',''); "
        "Base.metadata.create_all(create_engine(url)); c=Config(); "
        "c.set_main_option('script_location','app/db/alembic'); "
        "c.set_main_option('sqlalchemy.url',url); command.stamp(c,'head')",
        cwd=Path(__file__).resolve().parents[2],
        env=env,
    )
    assert await seed.wait() == 0
    env["AGENT_LB_DATABASE_MIGRATE_ON_STARTUP"] = "false"
    with (tmp_path / "server.log").open("w+") as log:
        process = subprocess.Popen(
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
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
                for _ in range(1200):
                    if process.poll() is not None:
                        log.seek(0)
                        pytest.fail(f"isolated proxy exited: {log.read()}")
                    try:
                        if (await client.get("/health/ready")).status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    await asyncio.sleep(0.1)
                else:
                    pytest.fail("isolated proxy never became ready")
                for account in ("fixture_turn_a", "fixture_turn_b"):
                    payload = {
                        "email": f"{account}@example.invalid",
                        "exp": int(time.time()) + 3600,
                        "https://api.openai.com/auth": {"chatgpt_account_id": account, "chatgpt_plan_type": "plus"},
                    }
                    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
                    auth = {
                        "tokens": {
                            "idToken": f"fixture.{body}.signature",
                            "accessToken": f"fixture.{body}.signature",
                            "refreshToken": "synthetic-fixture-refresh",
                            "accountId": account,
                        }
                    }
                    imported = await client.post(
                        "/api/accounts/import", files={"auth_json": ("auth.json", json.dumps(auth), "application/json")}
                    )
                    assert imported.status_code == 200, imported.text
                response = await client.post(
                    "/backend-api/codex/responses",
                    headers={"session_id": "turn-rate-fixture", "User-Agent": "codex_cli_rs/0.101.0"},
                    json={
                        "model": "gpt-5.2",
                        "input": [{"role": "user", "content": "Reply briefly."}],
                        "stream": True,
                        "store": False,
                        "prompt_cache_key": "turn-rate-fixture",
                    },
                )
                assert response.status_code == 200, response.text
                assert '"response.completed"' in response.text, response.text
                assert "turn_rate_limit_exceeded" not in response.text
                assert len(attempts) == 2 and attempts[0] != attempts[1], attempts
                followup = await client.post(
                    "/backend-api/codex/responses",
                    headers={"session_id": "turn-rate-fixture", "User-Agent": "codex_cli_rs/0.101.0"},
                    json={
                        "model": "gpt-5.2",
                        "input": [{"role": "user", "content": "Reply briefly again."}],
                        "stream": True,
                        "store": False,
                        "prompt_cache_key": "turn-rate-fixture",
                    },
                )
                assert followup.status_code == 200 and '"response.completed"' in followup.text, followup.text
                assert attempts == [attempts[0], attempts[1], attempts[1]], attempts
        finally:
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait)
            await runner.cleanup()
