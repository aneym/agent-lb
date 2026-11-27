from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

_ORIGIN = "https://dashboard.example.test:8455"
_SESSIONS = "/api/reauth/browser/sessions"
_INGRESS_PROOF = "public-test-fixture-used-only-for-reauth-tests"


@pytest.fixture
def browser_dashboard(tmp_path):
    proof_file = tmp_path / "ingress-proof"
    proof_file.write_text(_INGRESS_PROOF)
    key = Fernet.generate_key()
    crypto = Fernet(key)
    key_file = tmp_path / "fixture.key"
    key_file.write_bytes(key)
    key_file.chmod(0o600)
    database = tmp_path / "fixture.db"
    env = {
        **os.environ,
        "AGENT_LB_DATABASE_URL": f"sqlite+aiosqlite:///{database}",
        "AGENT_LB_DATA_DIR": str(tmp_path),
        "AGENT_LB_ENCRYPTION_KEY_FILE": str(key_file),
        "AGENT_LB_DASHBOARD_AUTH_MODE": "disabled",
        "AGENT_LB_DASHBOARD_KEYLESS_PROXY_PROOF_FILE": str(proof_file),
        "AGENT_LB_FIREWALL_TRUST_PROXY_HEADERS": "true",
        "AGENT_LB_FIREWALL_TRUSTED_PROXY_CIDRS": "127.0.0.1/32",
        "AGENT_LB_REAUTH_ORIGIN": _ORIGIN,
        "AGENT_LB_REAUTH_TRUSTED_CLIENT_IPS": "100.64.0.10",
        "AGENT_LB_STATE_DIR": str(tmp_path / "state"),
        "AGENT_LB_CHROME_ROOT": str(tmp_path / ".agent-lb-primary/browser-profiles"),
        "AGENT_LB_ACCOUNT_PULSE_ENABLED": "false",
    }
    with socket.socket() as listener, (tmp_path / "server.log").open("w") as log:
        listener.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        with subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--fd", str(listener.fileno()), "--no-proxy-headers"],
            env=env,
            pass_fds=(listener.fileno(),),
            stdout=log,
            stderr=log,
        ) as server:
            try:
                with httpx.Client(base_url=origin, timeout=2, trust_env=False) as client:
                    deadline = time.monotonic() + 25
                    while time.monotonic() < deadline and server.poll() is None:
                        try:
                            if client.get("/health/ready").is_success:
                                break
                        except (httpx.ConnectError, httpx.ReadTimeout):
                            pass
                        time.sleep(0.05)
                    else:
                        pytest.fail(f"Reauth service did not start: {log.name}")
                    yield client, database, crypto, tmp_path
            finally:
                server.terminate()
                server.wait(timeout=15)


@pytest.mark.e2e
def test_browser_requires_trusted_ingress_and_distinct_profile(browser_dashboard):
    client, database, crypto, root = browser_dashboard
    headers = {"host": "dashboard.example.test:8455", "origin": _ORIGIN}
    proof = {"x-agent-lb-ingress-proof": _INGRESS_PROOF, "x-forwarded-for": "100.64.0.10"}
    for rejected in (
        {**headers, "x-forwarded-for": "100.64.0.10"},
        {**headers, **proof, "x-forwarded-for": "100.64.0.20"},
        {**headers, **proof, "origin": "https://dashboard.example.test"},
    ):
        response = client.post(_SESSIONS, json={"accountId": "fixture"}, headers=rejected)
        assert response.status_code == 403, response.text

    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO accounts (id, email, provider, plan_type, access_token_encrypted, "
            "refresh_token_encrypted, last_refresh, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "fixture",
                "profile@example.test",
                "anthropic",
                "pro",
                crypto.encrypt(b"test"),
                crypto.encrypt(b""),
                "2026-01-01 00:00:00",
                "reauth_required",
            ),
        )

    def start():
        return client.post(_SESSIONS, json={"accountId": "fixture"}, headers={**headers, **proof})

    response = start()
    assert response.status_code == 409, response.text
    assert response.json()["error"]["message"] == "No registered browser profile for this account"
    registry = root / "state/browser-profiles.json"
    registry.parent.mkdir(exist_ok=True)
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": {
                    "profile@example.test": {"dir": "missing-profile", "providers": ["anthropic"], "cdpPort": 9333},
                },
            }
        )
    )
    response = start()
    assert response.status_code == 409, response.text
    assert response.json()["error"]["message"] == "registered profile directory is unavailable"
    assert not (Path(root) / ".agent-lb-primary/browser-profiles/missing-profile").exists()
