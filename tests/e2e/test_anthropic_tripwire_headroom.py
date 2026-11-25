from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import psycopg
import pytest


@pytest.mark.e2e
def test_weekly_reset_recovers_tripwire_and_headroom(tmp_path):
    if not os.environ.get("PG_CLUSTER_CONF_ROOT"):
        pytest.skip("Run with pg_virtualenv for a disposable PostgreSQL database")

    now = datetime.now(timezone.utc)
    env = {
        **os.environ,
        "AGENT_LB_DATA_DIR": str(tmp_path),
        "AGENT_LB_ENCRYPTION_KEY_FILE": str(tmp_path / "key"),
        "AGENT_LB_DATABASE_URL": (
            f"postgresql+asyncpg://{os.environ['PGUSER']}:{os.environ['PGPASSWORD']}"
            f"@127.0.0.1:{os.environ['PGPORT']}/{os.environ['PGDATABASE']}"
        ),
        "AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE": "false",
        "AGENT_LB_ANTHROPIC_UPSTREAM_BASE_URL": "http://127.0.0.1:1",
        "AGENT_LB_USAGE_REFRESH_ENABLED": "false",
    }
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    env["AGENT_LB_BASE_URL"] = base_url

    with (tmp_path / "agent-lb.log").open("w") as log:
        with subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
            env=env,
            stdout=log,
            stderr=log,
        ) as server:
            try:
                with httpx.Client(base_url=base_url, timeout=2, trust_env=False) as client:
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
                        for account in ("weekly01", "weekly02", "fivehour"):
                            db.execute(
                                """INSERT INTO accounts
                                   (id, email, provider, plan_type, access_token_encrypted,
                                    refresh_token_encrypted, last_refresh, status)
                                   VALUES (%s, %s, 'anthropic', 'claude', '', '', %s, 'active')""",
                                (account, f"{account}@example.invalid", now),
                            )
                            db.execute(
                                """INSERT INTO additional_usage_history
                                   (account_id, quota_key, limit_name, metered_feature, "window",
                                    used_percent, reset_at, recorded_at)
                                   VALUES (%s, 'anthropic_extra_usage', 'anthropic_extra_usage',
                                           'anthropic_messages', 'primary', 100, %s, %s)""",
                                (account, int((now + timedelta(days=5)).timestamp()), now - timedelta(days=2)),
                            )
                            _reading(db, account, "secondary", 2, now, now + timedelta(days=6))
                        _reading(db, "weekly01", "primary", 6, now - timedelta(days=20), now - timedelta(days=20))
                        _reading(db, "fivehour", "primary", 100, now - timedelta(minutes=1), now + timedelta(hours=2))

                        route = _route(client)
                        assert route.status_code == 200, route.text
                        assert route.json()["accountId"] in {"weekly01", "weekly02"}
                        original = _headroom(env, tmp_path)
                        assert original["weekly01"]["usable_now"] is True
                        assert original["weekly02"]["usable_now"] is True
                        assert original["fivehour"]["usable_now"] is False
                        assert "extra-usage tripwire active" in original["fivehour"]["note"]

                        for account in ("weekly01", "weekly02"):
                            _reading(db, account, "secondary", 100, now + timedelta(seconds=1), now + timedelta(days=6))
                        assert _route(client).status_code == 429

                        _reading(db, "fivehour", "primary", 0, now, now + timedelta(hours=5))
                        assert _route(client).json()["accountId"] == "fivehour"
                        recovered = _headroom(env, tmp_path)
                        assert recovered["fivehour"]["usable_now"] is True
                        assert recovered["weekly01"]["usable_now"] is False
                        assert recovered["weekly02"]["usable_now"] is False
            finally:
                server.terminate()
                server.wait(timeout=15)


def _route(client):
    return client.post(
        "/api/anthropic/session-route",
        json={"sessionId": str(uuid4()), "model": "claude-opus-5-5"},
    )


def _reading(db, account, window, percent, recorded_at, reset_at):
    db.execute(
        """INSERT INTO usage_history (account_id, provider, "window", used_percent, recorded_at, reset_at, window_minutes)
           VALUES (%s, 'anthropic', %s, %s, %s, %s, %s)""",
        (account, window, percent, recorded_at, int(reset_at.timestamp()), 300 if window == "primary" else 10080),
    )


def _headroom(env, directory):
    subprocess.run(
        [sys.executable, "-m", "app.usage_export", "--out", str(directory)],
        env=env,
        check=True,
        timeout=15,
    )
    document = json.loads((directory / "headroom.json").read_text())
    return {account["account"]: account for account in document["accounts_now"]}
