import asyncio
import json
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import aiohttp
from aiohttp import http_parser, web, web_protocol

sys.path.insert(0, str(Path.cwd()))
from scripts.bench_upstream import Fake, Upstream
from scripts.proxy_bench import environment


async def run():
    root = Path.cwd()
    data = root / ".local/alias-proof"
    data.mkdir(parents=True, exist_ok=True)
    for file in data.glob("store.db*"):
        file.unlink()
    http_parser.SINGLETON_HEADERS = http_parser.SINGLETON_HEADERS - {"content-type"}
    web_protocol.HttpRequestParser = http_parser.HttpRequestParserPy
    fake = Fake(SimpleNamespace(events=2, gap_ms=0, runs=100))
    observed = []

    async def upstream(request):
        import gzip

        raw = await request.read()
        if raw.startswith(b"\x1f\x8b"):
            raw = gzip.decompress(raw)
        if raw:
            body = json.loads(raw)
            observed.append({"path": request.path, "model": body.get("model")})
        return await fake.handle(request)

    server = Upstream(upstream)
    runner = web.ServerRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    assert site._server is not None
    fake_port = cast(asyncio.Server, site._server).sockets[0].getsockname()[1]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert port != 2455
    env = environment(data, f"http://127.0.0.1:{fake_port}")
    env["AGENT_LB_MODEL_ALIASES"] = json.dumps(
        {"work": "claude-sonnet-4-5", "fast": "gpt-5.4", "denied": "gpt-5.4-mini"}
    )
    env["AGENT_LB_EXCLUDED_MODEL_PATTERNS"] = '["gpt-5.4-mini", "claude-opus-*"]'
    env["AGENT_LB_SEAT_QUOTA_SCHEDULER_ENABLED"] = "false"
    log = (data / "server.log").open("w")
    child = subprocess.Popen(
        [sys.executable, "scripts/proxy_bench.py", "--serve", str(port)], env=env, stdout=log, stderr=log
    )
    results = {"base_url": f"http://127.0.0.1:{port}", "database": str(data / "store.db"), "checks": []}
    try:
        async with aiohttp.ClientSession() as client:
            base = results["base_url"]
            for _ in range(600):
                if child.poll() is not None:
                    raise RuntimeError((data / "server.log").read_text()[-5000:])
                try:
                    async with client.get(base + "/health/ready") as r:
                        if r.status == 200:
                            break
                except aiohttp.ClientError:
                    pass
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError("startup timeout")
            async with client.get(base + "/v1/models") as r:
                listing = await r.json()
                assert r.status == 200, listing
            entries = {x["id"]: x for x in listing["data"]}
            assert entries["work"]["target_model"] == "claude-sonnet-4-5"
            assert entries["fast"]["target_model"] == "gpt-5.4"
            assert "denied" not in entries and "gpt-5.4-mini" not in entries
            assert not any(x.startswith("claude-opus-") for x in entries)
            results["checks"].append("GET /v1/models: aliases point to targets; exclusions hidden")
            for model in ("work", "fast", "gpt-5.4"):
                async with client.get(base + "/v1/models/" + model) as r:
                    item = await r.json()
                    assert r.status == 200 and item["id"] == model, item
            for model in ("denied", "gpt-5.4-mini"):
                async with client.get(base + "/v1/models/" + model) as r:
                    assert r.status == 404
            results["checks"].append("GET /v1/models/{id}: alias and ordinary lookup; hidden IDs return 404")
            for index, (path, model) in enumerate(
                (
                    ("/v1/messages", "work"),
                    ("/v1/responses", "fast"),
                    ("/v1/messages", "claude-sonnet-4-5"),
                    ("/v1/responses", "gpt-5.4"),
                ),
                1,
            ):
                body: dict[str, Any] = {"model": model, "stream": True}
                key = f"bench-request-{index}"
                if path.endswith("messages"):
                    body.update(max_tokens=50, messages=[{"role": "user", "content": key}])
                else:
                    body.update(input=key, store=False)
                async with client.post(base + path, json=body, headers={"x-request-id": key}) as r:
                    text = await r.text()
                    assert r.status == 200, (r.status, text)
                    assert ("message_stop" if path.endswith("messages") else "response.completed") in text, text
                results["checks"].append(f"POST {path} model={model}: 200 complete stream")
            count = len(observed)
            for path in ("/v1/messages", "/v1/responses", "/v1/chat/completions"):
                for model in ("denied", "gpt-5.4-mini", "claude-opus-4-6"):
                    async with client.post(base + path, json={"model": model}) as r:
                        error = await r.json()
                        assert r.status == 400 and "excluded by operator policy" in error["error"]["message"], error
                        assert error["error"]["type"] == "invalid_request_error"
                        if path.endswith("messages"):
                            assert error["type"] == "error"
                        else:
                            assert error["error"]["code"] == "model_not_allowed"
            assert len(observed) == count
            assert [x["model"] for x in observed] == ["claude-sonnet-4-5", "gpt-5.4", "claude-sonnet-4-5", "gpt-5.4"], (
                observed
            )
            results["checks"].append("9 excluded inference requests: protocol-shaped 400; no upstream calls")
        child.terminate()
        await asyncio.to_thread(child.wait, 30)
        with sqlite3.connect(data / "store.db") as db:
            rows = db.execute(
                "select model, requested_model_alias, provider, status from request_logs order by id"
            ).fetchall()
        assert {(row[0], row[1], row[2], row[3]) for row in rows} == {
            ("claude-sonnet-4-5", "work", "anthropic", "success"),
            ("gpt-5.4", "fast", "openai", "success"),
            ("claude-sonnet-4-5", None, "anthropic", "success"),
            ("gpt-5.4", None, "openai", "success"),
        }, rows
        results.update(upstream=observed, request_logs=rows)
        results["checks"].append(
            "SQLite: target model/provider and original aliases retained; direct requests have null alias"
        )
        (data / "evidence.json").write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps(results, indent=2))
    finally:
        if child.poll() is None:
            child.terminate()
            await asyncio.to_thread(child.wait, 30)
        log.close()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(run())
