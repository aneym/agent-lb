from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx


def spare_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def seed(provider: str) -> None:
    from app.core.crypto import TokenEncryptor
    from app.db.models import Account
    from app.db.session import SessionLocal, close_db, init_db

    await init_db()
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        for index in range(4):
            session.add(
                Account(
                    id=f"proof-{index}",
                    provider=provider,
                    email=f"proof-{index}@example.invalid",
                    plan_type="pro",
                    access_token_encrypted=encryptor.encrypt(f"proof-{index}"),
                    refresh_token_encrypted=encryptor.encrypt(""),
                    last_refresh=datetime.now(),
                )
            )
        await session.commit()
    await close_db()


def run_case(provider: str, mode: str, root: Path) -> dict:
    calls: list[str] = []

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("content-length", 0)))
            calls.append(self.headers.get("authorization", ""))
            balance = mode == "balance"
            status = 403 if balance else 503
            body = (
                {
                    "error": {
                        "code": "1113" if provider == "glm" else "insufficient_balance",
                        "message": "Insufficient balance",
                    }
                }
                if balance
                else {"error": {"type": "api_error", "message": "Temporary outage"}}
            )
            if mode == "recover" and len(calls) > 1:
                status = 200
                body = {
                    "id": "proof",
                    "type": "message",
                    "role": "assistant",
                    "model": "proof",
                    "content": [{"type": "text", "text": "recovered"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    port = spare_port()
    data = root / f"{provider}-{mode}"
    data.mkdir()
    env = dict(os.environ)
    env["AGENT_LB_DATA_DIR"] = str(data)
    env["AGENT_LB_DATABASE_URL"] = f"sqlite+aiosqlite:///{data / 'store.db'}"
    env["AGENT_LB_ENCRYPTION_KEY_FILE"] = str(data / "encryption.key")
    env[f"AGENT_LB_{provider.upper()}_ANTHROPIC_UPSTREAM_BASE_URL"] = f"http://127.0.0.1:{upstream.server_port}"
    for setting in (
        "ACCOUNT_PULSE",
        "USAGE_REFRESH",
        "ACCOUNTS_CACHE_WARMER",
        "MODEL_REGISTRY",
        "RESET_CREDIT_AUTO_REDEEM",
        "QUOTA_PLANNER_SCHEDULER",
        "IDENTITY",
    ):
        env[f"AGENT_LB_{setting}_ENABLED"] = "false"
    env["AGENT_LB_DASHBOARD_BOOTSTRAP_TOKEN"] = "disposable-proof-bootstrap"
    env["PYTHONPATH"] = str(Path.cwd())
    subprocess.run(
        [sys.executable, __file__, "--seed", provider],
        env=env,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with (data / "server.log").open("w") as log:
        server = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; "
                "from app.core.forensics import register_stack_dump_signal; "
                f"register_stack_dump_signal(Path({str(data)!r})); "
                "import uvicorn; "
                f'uvicorn.run("app.main:app", host="127.0.0.1", port={port})',
            ],
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
                for _ in range(150):
                    if server.poll() is not None:
                        raise RuntimeError(f"Local server exited: {data / 'server.log'}")
                    try:
                        if client.get("/health/ready").status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Local server did not become ready")
                response = client.post(
                    "/v1/messages",
                    json={
                        "model": "glm-5" if provider == "glm" else "kimi-k2.5",
                        "max_tokens": 8,
                        "stream": False,
                        "messages": [{"role": "user", "content": "Hello"}],
                    },
                )
                result = {
                    "provider": provider,
                    "mode": mode,
                    "status": response.status_code,
                    "body": response.json(),
                    "upstream_calls": len(calls),
                    "distinct_accounts": len(set(calls)),
                }
                print(json.dumps(result), flush=True)
                (data / "result.json").write_text(json.dumps(result, indent=2) + "\n")
                if mode == "balance":
                    assert response.status_code == 503, result
                    assert response.json()["error"]["type"] == f"no_available_{provider}_accounts", result
                    assert len(calls) == 4 and len(set(calls)) == 4, result
                elif mode == "recover":
                    assert response.status_code == 200, result
                    assert len(calls) == 2 and len(set(calls)) == 2, result
                else:
                    assert response.status_code == 503, result
                    assert response.json()["error"]["type"] == "api_error", result
                    assert len(calls) == 4 and len(set(calls)) == 4, result
                return result
        finally:
            server.terminate()
            server.wait(timeout=20)
            upstream.shutdown()
            upstream.server_close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.seed:
        asyncio.run(seed(args.seed))
        return
    root = args.out or Path(tempfile.mkdtemp(prefix="balance-503-"))
    root.mkdir(parents=True, exist_ok=True)
    print(f"Artifacts: {root}", flush=True)
    for provider in ("glm", "kimi"):
        for mode in ("balance", "recover", "outage"):
            run_case(provider, mode, root)
    print("PASS: balance exhaustion, transient recovery and transient exhaustion over isolated HTTP")


if __name__ == "__main__":
    main()
