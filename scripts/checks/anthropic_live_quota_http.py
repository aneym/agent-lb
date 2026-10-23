from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]
ACCOUNT = "synthetic-anthropic-live-quota"


def request(url, body=None):
    req = Request(
        url, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"}
    )
    try:
        response = urlopen(req, timeout=15)
    except HTTPError as exc:
        response = exc
    with response:
        return response.status, json.load(response)


SEED = """
import asyncio
from datetime import timedelta
from app.db.session import init_db, init_background_db, SessionLocal, close_db
from app.db.models import Account
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.modules.usage.repository import UsageRepository
async def main():
    await init_db()
    init_background_db()
    async with SessionLocal() as session:
        encryptor = TokenEncryptor()
        session.add(Account(id="synthetic-anthropic-live-quota", provider="anthropic", email="quota@example.invalid",
                    plan_type="claude_max", access_token_encrypted=encryptor.encrypt("synthetic-token"),
                    refresh_token_encrypted=encryptor.encrypt("synthetic-refresh"), last_refresh=utcnow(),
                    access_expires_at=utcnow()+timedelta(days=1), limit_warmup_enabled=False))
        await session.commit()
        usage = UsageRepository(session)
        await usage.add_entry("synthetic-anthropic-live-quota",79,provider="anthropic",window="primary",reset_at=1999999999,window_minutes=300)
        await usage.add_entry("synthetic-anthropic-live-quota",34,provider="anthropic",window="secondary",reset_at=1999999999,window_minutes=10080)
    await close_db()
asyncio.run(main())
"""


def main():
    reset = int(time.time()) + 3600
    mode = {"value": "exhausted"}
    received = []
    delayed_usage = threading.Event()
    release_usage = threading.Event()
    race = {"armed": False}

    class Edge(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self):
            if race["armed"] and self.path.startswith("/api/oauth/usage"):
                race["armed"] = False
                delayed_usage.set()
                if not release_usage.wait(10):
                    raise RuntimeError("delayed usage response was not released")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {
                            "five_hour": {"utilization": 79, "resets_at": "2033-05-18T03:33:19Z"},
                            "seven_day": {"utilization": 34, "resets_at": "2033-05-18T03:33:19Z"},
                        }
                    ).encode()
                )
                return
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"type":"rate_limit_error","message":"usage endpoint throttled"}}')

        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0")))))
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            prefix = "anthropic-ratelimit-unified-"
            if mode["value"] == "exhausted":
                values = {
                    "5h-status": "rejected",
                    "5h-utilization": "1.0",
                    "5h-reset": str(reset),
                    "7d-status": "allowed",
                    "7d-utilization": "0.38",
                    "7d-reset": str(reset + 86400),
                }
            elif mode["value"] == "partial":
                values = {"5h-utilization": "NaN", "7d-utilization": "0.41"}
            elif mode["value"] == "model":
                values = {
                    "status": "rejected",
                    "5h-status": "allowed",
                    "5h-utilization": "0.2",
                    "7d-status": "allowed",
                    "7d-utilization": "0.4",
                    "5h-reset": str(reset),
                    "7d-reset": str(reset + 86400),
                }
            else:
                values = {}
            for key, value in values.items():
                self.send_header(prefix + key, value)
            self.end_headers()
            self.wfile.write(b'{"error":{"type":"rate_limit_error","message":"quota exhausted"}}')

    with tempfile.TemporaryDirectory(prefix="lb-quota-http-") as directory:
        edge = ThreadingHTTPServer(("127.0.0.1", 0), Edge)
        threading.Thread(target=edge.serve_forever, daemon=True).start()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {key: value for key, value in os.environ.items() if not key.startswith("AGENT_LB_")}
        env.update(
            {
                "TZ": "America/New_York",
                "AGENT_LB_DATA_DIR": directory,
                "AGENT_LB_ANTHROPIC_UPSTREAM_BASE_URL": f"http://127.0.0.1:{edge.server_port}",
                "AGENT_LB_UPSTREAM_BASE_URL": f"http://127.0.0.1:{edge.server_port}",
                "AGENT_LB_DASHBOARD_AUTH_MODE": "disabled",
                "AGENT_LB_API_KEY_AUTH_ENABLED": "false",
            }
        )
        for setting in (
            "usage_refresh",
            "account_pulse",
            "accounts_cache_warmer",
            "reset_credit_auto_redeem",
            "quota_planner_scheduler",
            "sticky_session_cleanup",
            "model_registry",
            "identity",
        ):
            env[f"AGENT_LB_{setting.upper()}_ENABLED"] = "false"
        subprocess.run([sys.executable, "-c", SEED], cwd=ROOT, env=env, check=True)
        with open(Path(directory) / "service.log", "w+") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=log,
            )
            try:
                base = f"http://127.0.0.1:{port}"
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError("isolated service exited")
                    try:
                        status, before = request(base + "/api/accounts")
                        if status == 200:
                            break
                    except OSError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("isolated service did not start")
                print("before", json.dumps(before), flush=True)
                status, probe = request(base + f"/api/accounts/{ACCOUNT}/probe", {})
                print("probe", status, json.dumps(probe), flush=True)
                assert status == 200 and probe["probeStatusCode"] == 429, probe
                assert probe["primaryUsedPercentAfter"] == 100, probe
                assert probe["secondaryUsedPercentAfter"] == 38, probe
                assert probe["accountStatusAfter"] == "rate_limited", probe
                _, after = request(base + "/api/accounts")
                account = after["accounts"][0]
                assert account["usage"]["primaryRemainingPercent"] == 0, account
                assert account["usage"]["secondaryRemainingPercent"] == 62, account
                assert account["rateLimitResetAt"].startswith(time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(reset))), (
                    account
                )
                process.terminate()
                process.wait(timeout=15)
                race_env = dict(env)
                race_env["AGENT_LB_USAGE_REFRESH_ENABLED"] = "true"
                process = subprocess.Popen(
                    [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                    cwd=ROOT,
                    env=race_env,
                    stdout=log,
                    stderr=log,
                )
                for _ in range(100):
                    try:
                        if request(base + "/api/accounts")[0] == 200:
                            break
                    except OSError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("race service did not start")
                with socket.socket() as sock:
                    sock.bind(("127.0.0.1", 0))
                    observer_port = sock.getsockname()[1]
                observer_process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "app.main:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(observer_port),
                    ],
                    cwd=ROOT,
                    env=env,
                    stdout=log,
                    stderr=log,
                )
                observer_base = f"http://127.0.0.1:{observer_port}"
                try:
                    for _ in range(100):
                        try:
                            if request(observer_base + "/api/accounts")[0] == 200:
                                break
                        except OSError:
                            pass
                        time.sleep(0.1)
                    else:
                        raise RuntimeError("observer service did not start")
                    race["armed"] = True
                    concurrent_result = []
                    pending = threading.Thread(
                        target=lambda: concurrent_result.append(request(base + f"/api/accounts/{ACCOUNT}/probe", {}))
                    )
                    pending.start()
                    assert delayed_usage.wait(5), "usage request never reached upstream edge"
                    _, unchanged = request(observer_base + f"/api/accounts/{ACCOUNT}/probe", {})
                    assert unchanged["primaryUsedPercentAfter"] == 100, unchanged
                    mode["value"] = "missing"
                    release_usage.set()
                    pending.join(timeout=10)
                    assert not pending.is_alive(), "delayed probe did not finish"
                    assert concurrent_result[0][0] == 200, concurrent_result
                    _, after_race = request(base + "/api/accounts")
                    race_account = after_race["accounts"][0]
                    assert race_account["usage"]["primaryRemainingPercent"] == 0, race_account
                    assert race_account["usage"]["secondaryRemainingPercent"] == 62, race_account
                    assert race_account["rateLimitResetAt"] == account["rateLimitResetAt"], race_account
                    print(
                        "PASS delayed stale usage response cannot replace newer unchanged quota header observation",
                        flush=True,
                    )
                finally:
                    release_usage.set()
                    observer_process.terminate()
                    observer_process.wait(timeout=15)
                mode["value"] = "partial"
                _, partial = request(base + f"/api/accounts/{ACCOUNT}/probe", {})
                assert partial["primaryUsedPercentAfter"] == 100 and partial["secondaryUsedPercentAfter"] == 41, partial
                _, after_partial = request(base + "/api/accounts")
                assert after_partial["accounts"][0]["resetAtSecondary"] == account["resetAtSecondary"]
                mode["value"] = "missing"
                _, missing = request(base + f"/api/accounts/{ACCOUNT}/probe", {})
                assert missing["primaryUsedPercentAfter"] == 100 and missing["secondaryUsedPercentAfter"] == 41, missing
                mode["value"] = "model"
                _, model = request(base + f"/api/accounts/{ACCOUNT}/probe", {})
                assert model["primaryUsedPercentAfter"] == 20 and model["secondaryUsedPercentAfter"] == 40, model
                status, _ = request(base + f"/api/accounts/{ACCOUNT}/reactivate", {})
                assert status == 200
                body = {
                    "model": "claude-sonnet-4-5",
                    "max_tokens": 4,
                    "messages": [{"role": "user", "content": "Reply OK only."}],
                    "stream": False,
                }
                status, result = request(base + "/v1/messages", body)
                assert status == 429, (status, result)
                _, model_after = request(base + "/api/accounts")
                model_account = model_after["accounts"][0]
                assert model_account["usage"]["primaryRemainingPercent"] == 80, model_account
                assert model_account["usage"]["secondaryRemainingPercent"] == 60, model_account
                assert model_account["rateLimitResetAt"] != account["rateLimitResetAt"], model_account
                status, _ = request(base + f"/api/accounts/{ACCOUNT}/reactivate", {})
                assert status == 200
                process.terminate()
                process.wait(timeout=15)
                process = subprocess.Popen(
                    [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                    cwd=ROOT,
                    env=env,
                    stdout=log,
                    stderr=log,
                )
                for _ in range(100):
                    try:
                        if request(base + "/api/accounts")[0] == 200:
                            break
                    except OSError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("isolated service did not restart")
                body["model"] = "claude-haiku-4-5"
                mode["value"] = "exhausted"
                status, result = request(base + "/v1/messages", body)
                assert status == 429, (status, result)
                _, proxy_after = request(base + "/api/accounts")
                proxy_account = proxy_after["accounts"][0]
                print("proxy", json.dumps(proxy_account), flush=True)
                assert proxy_account["usage"]["primaryRemainingPercent"] == 0, proxy_account
                assert proxy_account["usage"]["secondaryRemainingPercent"] == 62, proxy_account
                assert proxy_account["rateLimitResetAt"] == account["rateLimitResetAt"], proxy_account
                assert all(item["messages"] == body["messages"] for item in received), received
                print(
                    "PASS proxy+operator429 persist usage/reset/status; model-only429 stays scoped; partial/NaN/missing headers preserve known values",
                    flush=True,
                )
            except BaseException:
                print(
                    "Isolated service diagnostics withheld because startup logs contain bootstrap credentials",
                    file=sys.stderr,
                )
                raise
            finally:
                process.terminate()
                process.wait(timeout=15)
                edge.shutdown()


if __name__ == "__main__":
    main()
