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

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.checks.anthropic_live_quota_http import SEED, request

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]
ACCOUNT = "synthetic-kimi-quota"


def main():
    reset = int(time.time()) + 1800
    monthly_reset = int(time.time()) + 86400 * 20
    iso = lambda epoch: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))
    mode = {"value": "measured"}
    usage_reads = []

    class Edge(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self):
            assert self.path == "/coding/v1/usages", self.path
            usage_reads.append(self.path)
            self.send_response(200 if mode["value"] != "error" else 429)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = {
                "limits": [
                    {
                        "window": {"duration": 300, "timeUnit": "TIME_UNIT_MINUTE"},
                        "detail": {"limit": "100", "used": "100", "resetTime": iso(reset)},
                    }
                ],
                "usages": {
                    "limit_5h": {"used_ratio": 1, "reset_time": iso(reset)},
                    "limit_month_total": {"used_ratio": 0.0305, "reset_time": iso(monthly_reset)},
                    "limit_month_code": {"used_ratio": 0.0305, "reset_time": iso(monthly_reset)},
                },
            }
            if mode["value"] != "measured":
                payload = {"usages": {"limit_5h": {"used_ratio": "invalid"}}}
            self.wfile.write(json.dumps(payload).encode())

        def do_POST(self):
            assert self.path == "/coding/v1/messages", self.path
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"type":"rate_limit_error","message":"5-hour limit exhausted"}}')

    with tempfile.TemporaryDirectory(prefix="lb-kimi-usage-") as directory:
        edge = ThreadingHTTPServer(("127.0.0.1", 0), Edge)
        threading.Thread(target=edge.serve_forever, daemon=True).start()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {key: value for key, value in os.environ.items() if not key.startswith("AGENT_LB_")}
        env.update(
            {
                "AGENT_LB_DATA_DIR": directory,
                "AGENT_LB_DASHBOARD_AUTH_MODE": "disabled",
                "AGENT_LB_KIMI_ANTHROPIC_UPSTREAM_BASE_URL": f"http://127.0.0.1:{edge.server_port}/coding",
                "TZ": "America/New_York",
                "AGENT_LB_ADDITIONAL_QUOTA_REGISTRY_FILE": str(ROOT / "config/additional_quota_registry.json"),
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
        seed = (
            SEED.replace("synthetic-anthropic-live-quota", ACCOUNT)
            .replace('provider="anthropic"', 'provider="kimi"')
            .replace('plan_type="claude_max"', 'plan_type="kimi-coding"')
        )
        seed = (
            seed[: seed.index("        usage = UsageRepository(session)")]
            + "\n    await close_db()\nasyncio.run(main())\n"
        )
        subprocess.run([sys.executable, "-c", seed], cwd=ROOT, env=env, check=True)
        env["AGENT_LB_USAGE_REFRESH_ENABLED"] = "true"
        env["AGENT_LB_USAGE_REFRESH_INTERVAL_SECONDS"] = "3600"
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
                        raise RuntimeError("isolated Kimi service exited; startup logs withheld")
                    try:
                        if request(base + "/api/accounts")[0] == 200:
                            break
                    except OSError:
                        pass
                    time.sleep(0.1)
                status, probe = request(base + f"/api/accounts/{ACCOUNT}/probe", {})
                assert status == 200 and probe["probeStatusCode"] == 429, probe
                _, data = request(base + "/api/accounts")
                account = data["accounts"][0]
                print("measured", json.dumps(account), flush=True)
                assert usage_reads, "actual provider usage endpoint never fetched"
                assert account["usage"]["primaryRemainingPercent"] == 0, account
                assert account["usage"]["monthlyRemainingPercent"] == 96.95, account
                assert account["usage"]["secondaryRemainingPercent"] is None, account
                assert account["resetAtPrimary"] == iso(reset), account
                assert account["resetAtMonthly"] == iso(monthly_reset), account
                assert account["status"] == "rate_limited", account
                time.sleep(2)
                _, data = request(base + "/api/accounts")
                account = data["accounts"][0]
                additional = {item["quotaKey"] for item in account["additionalQuotas"]}
                assert {"kimi_month_total", "kimi_month_code"}.issubset(additional), additional
                for mode_name in ("malformed", "error"):
                    mode["value"] = mode_name
                    request(base + f"/api/accounts/{ACCOUNT}/probe", {})
                    _, preserved = request(base + "/api/accounts")
                    assert preserved["accounts"][0]["usage"] == account["usage"], preserved
                    assert preserved["accounts"][0]["status"] not in ("reauth_required", "deactivated"), preserved
                print(
                    "PASS real Kimi usage HTTP -> measured5h/monthly/reset/account; malformed/error preserve telemetry and auth",
                    flush=True,
                )
            finally:
                process.terminate()
                process.wait(timeout=15)
                edge.shutdown()


if __name__ == "__main__":
    main()
