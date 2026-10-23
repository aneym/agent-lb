from __future__ import annotations

import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import aiohttp
from aiohttp import web

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


async def seed() -> None:
    from app.core.auth import generate_unique_account_id
    from app.core.crypto import TokenEncryptor
    from app.core.providers.anthropic_compat import get_anthropic_compat_profile
    from app.core.utils.time import utcnow
    from app.db.models import Account, AccountStatus
    from app.db.session import SessionLocal, close_db, init_db

    await init_db()
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        for provider in ("kimi", "glm", "openrouter"):
            defaults = get_anthropic_compat_profile(provider).import_defaults
            assert defaults is not None
            session.add(
                Account(
                    id=generate_unique_account_id(defaults.account_id_seed, defaults.email),
                    provider=provider,
                    email=defaults.email,
                    chatgpt_account_id=defaults.account_id_seed,
                    plan_type=defaults.plan_type,
                    access_token_encrypted=encryptor.encrypt(f"fake-{provider}-key"),
                    refresh_token_encrypted=encryptor.encrypt(f"fake-{provider}-key"),
                    last_refresh=utcnow(),
                    status=AccountStatus.ACTIVE,
                    alias=f"legacy-{provider}",
                )
            )
        await session.commit()
    await close_db()


def serve(port: int) -> None:
    upstream_port = int(os.environ["PROXY_BENCH_UPSTREAM_PORT"])

    def guard(event, values):
        if event == "socket.connect":
            address = values[1]
            if isinstance(address, tuple) and address[:2] != ("127.0.0.1", upstream_port):
                raise RuntimeError("non-fixture egress blocked")

    sys.addaudithook(guard)
    asyncio.run(seed())
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="error", access_log=False)


async def check() -> None:
    from scripts.proxy_bench import environment

    mode = "refuse"
    completion = {
        "id": "fake",
        "choices": [{"message": {"role": "assistant", "content": "hello"}}],
        "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
    }
    calls = 0
    body = (
        b'data: {"id":"fake","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"hello"}}]}\n\n'
        b'data: {"id":"fake","choices":[],"usage":{"prompt_tokens":4,"completion_tokens":1,"total_tokens":5}}\n\n'
        b"data: [DONE]\n\n"
    )

    async def upstream(request):
        nonlocal calls
        calls += 1
        if mode == "refuse":
            return web.json_response({"error": {"message": "Your plan supports only kimi-for-coding"}}, status=403)
        if mode == "server-error":
            return web.json_response({"error": {"message": "fixture unavailable"}}, status=500)
        if mode == "disconnect":
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(body[: body.index(b'data: {"id":"fake","choices":[]')])
            request.transport.close()
            return response
        if not (await request.json()).get("stream"):
            return web.json_response(completion)
        return web.Response(body=body, content_type="text/event-stream")

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", upstream)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    assert isinstance(site._server, asyncio.Server)
    upstream_port = site._server.sockets[0].getsockname()[1]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert port != 2455
    try:
        with tempfile.TemporaryDirectory(prefix="kimi-refusal-") as directory:
            data = Path(directory)
            env = environment(data, f"http://127.0.0.1:{upstream_port}")
            env["AGENT_LB_PROXY_POOL_WAIT_ENABLED"] = "false"
            env["AGENT_LB_UPSTREAM_CONNECT_ATTEMPTS"] = "1"
            with (data / "server.log").open("w") as log:
                child = subprocess.Popen(
                    [sys.executable, __file__, "--serve", str(port)], cwd=ROOT, env=env, stdout=log, stderr=log
                )
                try:
                    async with aiohttp.ClientSession() as client:
                        base = f"http://127.0.0.1:{port}"
                        for _ in range(300):
                            if child.poll() is not None:
                                raise RuntimeError(
                                    "isolated server exited: " + (data / "server.log").read_text()[-4000:]
                                )
                            try:
                                async with client.get(base + "/api/dashboard-auth/session") as response:
                                    if response.status == 200:
                                        break
                            except aiohttp.ClientError:
                                pass
                            await asyncio.sleep(0.1)
                        else:
                            raise RuntimeError("isolated server not ready")
                        db = sqlite3.connect(data / "store.db")
                        originals = dict(db.execute("select provider,id from accounts"))
                        for provider, account_id in originals.items():
                            payload = {"provider": provider, "apiKey": f"fake-{provider}-key", "alias": "updated"}
                            if provider == "openrouter":
                                payload["creditsBalance"] = 10
                            for _ in range(2):
                                async with client.post(base + "/api/accounts/import/api-key", json=payload) as response:
                                    result = await response.json()
                                    assert response.status == 200, ("import", provider, response.status)
                                    assert result["accountId"] == account_id, ("legacy identity changed", provider)
                            assert (
                                db.execute("select count(*) from accounts where provider=?", (provider,)).fetchone()[0]
                                == 1
                            )
                            assert (
                                db.execute("select alias from accounts where id=?", (account_id,)).fetchone()[0]
                                == "updated"
                            )
                        print(
                            "PASS legacy Kimi, GLM, OpenRouter re-import preserves account ID and updates alias",
                            flush=True,
                        )
                        async with client.post(
                            base + "/api/accounts/import/api-key",
                            json={
                                "provider": "kimi",
                                "apiKey": "fake-distinct-key",
                            },
                        ) as response:
                            result = await response.json()
                            assert response.status == 200
                            assert result["accountId"] != originals["kimi"]
                        assert db.execute("select count(*) from accounts where provider='kimi'").fetchone()[0] == 2
                        print("PASS distinct key creates separate account", flush=True)
                        async with client.post(
                            base + "/api/api-keys/",
                            json={
                                "name": "isolated-proof",
                                "assignedAccountIds": [originals["kimi"]],
                                "limits": [{"limitType": "total_tokens", "limitWindow": "daily", "maxValue": 1000000}],
                            },
                        ) as response:
                            result = await response.json()
                            assert response.status == 200, ("create key", response.status)
                            headers = {"Authorization": "Bearer " + result["key"]}
                        for stream in (True, False, True):
                            async with client.post(
                                base + "/v1/chat/completions",
                                headers=headers,
                                json={
                                    "model": "kimi-for-coding",
                                    "stream": stream,
                                    "messages": [{"role": "user", "content": "hello"}],
                                    "max_tokens": 10,
                                },
                            ) as response:
                                assert response.status == 403, ("refusal HTTP status", response.status)
                                result = await response.json()
                                assert result["error"]["type"] == "permission_error", result
                            assert (
                                db.execute(
                                    "select count(*) from api_key_usage_reservations where status='reserved'"
                                ).fetchone()[0]
                                == 0
                            )
                        assert db.execute("select count(*) from api_key_usage_reservations").fetchone()[0] >= 3
                        print(
                            "PASS repeated streaming and nonstream refusals return HTTP 403 permission_error; reservations released",
                            flush=True,
                        )
                        mode = "success"
                        for stream in (True, False):
                            async with client.post(
                                base + "/v1/chat/completions",
                                headers=headers,
                                json={
                                    "model": "kimi-for-coding",
                                    "stream": stream,
                                    "messages": [{"role": "user", "content": "hello"}],
                                    "max_tokens": 10,
                                },
                            ) as response:
                                received = await response.read()
                                assert response.status == 200, (stream, response.status, received.decode())
                                if stream:
                                    assert received == body, "priming dropped or duplicated bytes"
                                else:
                                    assert json.loads(received) == completion
                            assert (
                                db.execute(
                                    "select count(*) from api_key_usage_reservations where status='reserved'"
                                ).fetchone()[0]
                                == 0
                            )
                        assert (
                            db.execute("select status from accounts where id=?", (originals["kimi"],)).fetchone()[0]
                            == "active"
                        )
                        print("PASS successful stream bytes preserved and account remains active", flush=True)
                        mode = "disconnect"
                        async with client.post(
                            base + "/v1/chat/completions",
                            headers=headers,
                            json={
                                "model": "kimi-for-coding",
                                "stream": True,
                                "messages": [{"role": "user", "content": "hello"}],
                                "max_tokens": 10,
                            },
                        ) as response:
                            try:
                                await response.read()
                            except aiohttp.ClientPayloadError:
                                pass
                        for _ in range(100):
                            if (
                                db.execute(
                                    "select count(*) from api_key_usage_reservations where status='reserved'"
                                ).fetchone()[0]
                                == 0
                            ):
                                break
                            await asyncio.sleep(0.05)
                        else:
                            raise AssertionError("interrupted upstream leaked reservation")
                        print("PASS interrupted upstream releases reservation", flush=True)
                        print(f"PASS isolated HTTP proof; upstream calls={calls}; live port untouched", flush=True)
                        db.close()
                finally:
                    child.terminate()
                    child.wait(timeout=20)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--serve":
        serve(int(sys.argv[2]))
    else:
        asyncio.run(check())
