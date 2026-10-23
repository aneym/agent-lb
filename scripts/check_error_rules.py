from __future__ import annotations

import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def seed(provider: str) -> None:
    from app.core.crypto import TokenEncryptor
    from app.db.models import Account, AccountStatus
    from app.db.session import SessionLocal, close_db, init_db

    await init_db()
    encryptor = TokenEncryptor()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with SessionLocal() as session:
        session.add(
            Account(
                id="error-rules-proof",
                provider=provider,
                email="proof@example.invalid",
                plan_type=f"{provider}-coding",
                access_token_encrypted=encryptor.encrypt("fake-upstream"),
                refresh_token_encrypted=encryptor.encrypt("fake-upstream"),
                last_refresh=now,
                access_expires_at=now + timedelta(days=30),
                status=AccountStatus.ACTIVE,
            )
        )
        await session.commit()
    await close_db()


def serve() -> None:
    upstream_port = int(os.environ["ERROR_RULES_UPSTREAM_PORT"])

    def guard(event, values):
        if event == "socket.connect":
            address = values[1]
            if isinstance(address, tuple) and address[:2] != ("127.0.0.1", upstream_port):
                raise RuntimeError("proof forbids external connections")

    sys.addaudithook(guard)
    asyncio.run(seed(sys.argv[3]))
    import uvicorn

    from app.core.forensics import register_stack_dump_signal

    register_stack_dump_signal(Path(os.environ["AGENT_LB_DATA_DIR"]) / "forensics")
    uvicorn.run("app.main:app", host="127.0.0.1", port=int(sys.argv[2]), log_level="error", access_log=False)


async def scenario(name, provider, upstream_status, error, expected, rules=()):
    data = ROOT / ".local" / "error-rules" / name
    data.mkdir(parents=True, exist_ok=True)
    database = data / "store.db"
    if database.exists():
        database.unlink()
    calls = 0

    async def upstream(request):
        nonlocal calls
        calls += 1
        return web.json_response({"error": error}, status=upstream_status)

    app = web.Application()
    app.router.add_post("/v1/messages", upstream)
    runner = web.AppRunner(app)
    await runner.setup()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    upstream_port = listener.getsockname()[1]
    await web.SockSite(runner, listener).start()
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]
    if 2455 in (port, upstream_port):
        raise RuntimeError("live port refused")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AGENT_LB_") and key.lower() not in ("http_proxy", "https_proxy", "all_proxy")
    }
    settings = {
        "data_dir": str(data),
        "database_url": f"sqlite+aiosqlite:///{database}",
        "encryption_key_file": str(data / "encryption.key"),
        "glm_anthropic_upstream_base_url": f"http://127.0.0.1:{upstream_port}",
        "kimi_anthropic_upstream_base_url": f"http://127.0.0.1:{upstream_port}",
        "proxy_unauthenticated_client_cidrs": "127.0.0.1/32",
        "identity_enabled": "false",
        "dashboard_bootstrap_token": "local-proof-placeholder",
        "upstream_error_rules": json.dumps(rules),
    }
    for feature in (
        "account_pulse",
        "usage_refresh",
        "model_registry",
        "auth_guardian",
        "reset_credit_auto_redeem",
        "reset_credit_expiry_redeem",
        "accounts_cache_warmer",
        "quota_planner_scheduler",
        "metrics",
        "otel",
        "leader_election",
    ):
        settings[f"{feature}_enabled"] = "false"
    env.update({f"AGENT_LB_{key.upper()}": value for key, value in settings.items()})
    env["ERROR_RULES_UPSTREAM_PORT"] = str(upstream_port)
    env["NO_PROXY"] = "*"
    with (data / "server.log").open("w") as log:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--serve", str(port), provider],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90)) as client:
                for _ in range(300):
                    if child.poll() is not None:
                        raise RuntimeError(f"server exited; see {data / 'server.log'}")
                    try:
                        async with client.get(f"http://127.0.0.1:{port}/health"):
                            break
                    except aiohttp.ClientConnectionError:
                        await asyncio.sleep(0.1)
                else:
                    raise RuntimeError("server failed to start")
                body = {
                    "model": "glm-5.2" if provider == "glm" else "kimi-for-coding",
                    "max_tokens": 16,
                    "stream": True,
                    "messages": [{"role": "user", "content": "proof"}],
                }
                async with client.post(f"http://127.0.0.1:{port}/v1/messages", json=body) as response:
                    downstream_status = response.status
                    result = await response.text()
                with sqlite3.connect(database) as connection:
                    state = connection.execute("SELECT status, deactivation_reason FROM accounts").fetchone()
                    logs = connection.execute("SELECT error_code FROM request_logs").fetchall()
                if (
                    tuple(state) != expected
                    or calls < 1
                    or (downstream_status < 400 and '"type": "error"' not in result)
                ):
                    raise AssertionError((name, state, expected, calls, downstream_status, result))
                if name == "custom-cooldown":
                    prior_calls = calls
                    async with client.post(f"http://127.0.0.1:{port}/v1/messages", json=body) as retry:
                        await retry.read()
                    if calls != prior_calls:
                        raise AssertionError("cooldown account was selected again")
                if name == "custom-fail" and (calls != 1 or not any(row[0] == "upstream_rule" for row in logs)):
                    raise AssertionError(f"fail_request retried: {calls}")
                if name == "unmatched-500" and not any(row[0] == "upstream_500" for row in logs):
                    raise AssertionError(f"unmatched failure bypassed normal handling: {logs}")
                print(
                    json.dumps(
                        {
                            "case": name,
                            "upstream_status": upstream_status,
                            "downstream_status": downstream_status,
                            "account_status": state[0],
                            "reason": state[1],
                            "upstream_calls": calls,
                            "request_errors": [row[0] for row in logs],
                        }
                    ),
                    flush=True,
                )
        finally:
            child.terminate()
            await asyncio.to_thread(child.wait, 20)
            await runner.cleanup()


async def main():
    await scenario("glm-1113", "glm", 400, {"code": 1113}, ("paused", "balance_exhausted: 1113"))
    await scenario(
        "kimi-balance",
        "kimi",
        403,
        {"type": "insufficient_balance"},
        ("paused", "balance_exhausted: insufficient_balance"),
    )
    await scenario("unmatched-500", "glm", 500, {"code": "unknown", "message": "temporary failure"}, ("active", None))
    await scenario(
        "kimi-suspended",
        "kimi",
        401,
        {"code": "insufficient_balance", "message": "SUSPENDED"},
        ("paused", "balance_exhausted: suspended"),
    )
    await scenario(
        "kimi-message",
        "kimi",
        500,
        {"message": "Extra usage balance has run out"},
        ("paused", "balance_exhausted: insufficient_balance"),
    )
    await scenario("kimi-unmatched-status", "kimi", 400, {"code": "insufficient_balance"}, ("active", None))
    await scenario(
        "custom-fail",
        "glm",
        400,
        {"code": 1113, "message": "operator override"},
        ("active", None),
        [
            {
                "provider": "glm",
                "http_status": 400,
                "error_code": "1113",
                "message_regex": "override$",
                "action": "fail_request",
                "reason": "operator refusal",
            }
        ],
    )
    await scenario(
        "custom-pause",
        "glm",
        500,
        {"code": "custom"},
        ("paused", "operator pause"),
        [
            {
                "provider": "glm",
                "http_status": 500,
                "error_code": "custom",
                "action": "pause",
                "reason": "operator pause",
            }
        ],
    )

    await scenario(
        "custom-cooldown",
        "glm",
        400,
        {"code": "custom"},
        ("rate_limited", None),
        [
            {
                "provider": "glm",
                "http_status": 400,
                "error_code": "custom",
                "action": "cooldown",
                "reason": "operator cooldown",
                "cooldown_seconds": 300,
            }
        ],
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        serve()
    else:
        asyncio.run(main())
