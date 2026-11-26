from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import httpx
import psycopg
import pytest

MODEL = "claude-fable-5-2"


@pytest.mark.e2e
def test_fable_hold_does_not_hide_eligible_fallback(tmp_path):
    if not os.environ.get("PG_CLUSTER_CONF_ROOT"):
        pytest.skip("Run with pg_virtualenv for a disposable PostgreSQL database")
    now = datetime.now(timezone.utc)
    reset = int((now + timedelta(hours=3)).timestamp())
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {
        **os.environ,
        "AGENT_LB_DATA_DIR": str(tmp_path),
        "AGENT_LB_DATABASE_URL": (
            f"postgresql+asyncpg://{os.environ['PGUSER']}:{os.environ['PGPASSWORD']}"
            f"@127.0.0.1:{os.environ['PGPORT']}/{os.environ['PGDATABASE']}"
        ),
        "AGENT_LB_DASHBOARD_AUTH_MODE": "disabled",
        "AGENT_LB_MODEL_ACCOUNT_EXCLUSIONS": json.dumps({MODEL: {"held": reset + 3600}}),
        "AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE": "false",
        "AGENT_LB_ANTHROPIC_UPSTREAM_BASE_URL": "http://127.0.0.1:1",
    }
    with subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) as server:
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2, trust_env=False) as client:
                for _ in range(200):
                    if server.poll() is not None:
                        pytest.fail("Fixture service exited before it was ready")
                    try:
                        if client.get("/health/ready").is_success:
                            break
                    except httpx.ConnectError:
                        pass
                    time.sleep(0.1)
                else:
                    pytest.fail("Fixture service never became ready")
                with psycopg.connect(autocommit=True) as db:
                    _seed(db, now, reset)
                    route = client.post(
                        "/api/anthropic/session-route", json={"sessionId": "fable-test", "model": MODEL}
                    )
                    assert route.status_code == 200, route.text
                    assert route.json()["accountId"] == "fallback"
                    db.execute("UPDATE usage_history SET used_percent = 100 WHERE account_id = 'fallback'")
                refused = client.post(
                    "/v1/messages",
                    headers={"user-agent": "claude-cli/2.1.329 (external, t3-code, agent-sdk/0.3.321)"},
                    json={"model": MODEL, "max_tokens": 32, "messages": [{"role": "user", "content": "OK?"}]},
                )
                assert refused.status_code == 429, refused.text
                assert "All-model weekly quota is exhausted" in refused.json()["error"]["message"]
                assert "Operator holds exclude 1" in refused.json()["error"]["message"]
                logs = client.get("/api/request-logs", params={"model": MODEL, "limit": 1})
                row = logs.json()["requests"][0]
                assert row["accountId"] is None
                assert row["errorCode"] == "anthropic_quota_cooldown"
        finally:
            server.terminate()
            server.wait(timeout=15)


def _seed(db, now, reset):
    for account, status, used in (
        ("held", "active", 10),
        ("fallback", "active", 80),
        ("signed-out", "reauth_required", 0),
    ):
        db.execute(
            """INSERT INTO accounts (id, email, provider, plan_type, access_token_encrypted,
                       refresh_token_encrypted, last_refresh, status)
               VALUES (%s, %s, 'anthropic', 'claude', '', '', %s, %s)""",
            (account, f"{account}@example.invalid", now, status),
        )
        db.execute(
            """INSERT INTO usage_history (account_id, provider, "window", used_percent, recorded_at, reset_at)
               VALUES (%s, 'anthropic', 'secondary', %s, %s, %s)""",
            (account, used, now, reset),
        )
    key = f"claude:anthropic_top_thinking_fable:session:{sha256(b'fable-test').hexdigest()[:32]}"
    db.execute("INSERT INTO sticky_sessions (key, account_id, kind) VALUES (%s, 'signed-out', 'codex_session')", (key,))
