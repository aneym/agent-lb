from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import psycopg
import pytest
from cryptography.fernet import Fernet

T3_UA = "claude-cli/2.1.329 (external, t3-code, agent-sdk/0.3.321)"
SDK_CLI_UA = "claude-cli/2.1.327 (external, sdk-cli, agent-sdk/0.3.321)"
DELEGATED_THREAD = "thread:delegated-task:command%3Amcp%3A11111111-2222-3333-4444-555555555555%3Adelegate-task%3Aplan-1"
TOP_LEVEL_THREAD = "mcp:launch:22222222-3333-4444-5555-666666666666"
UPSTREAM_RESPONSE = json.dumps(
    {
        "id": "msg_cache_policy",
        "type": "message",
        "role": "assistant",
        "model": "claude-fable-5-2",
        "content": [{"type": "text", "text": "ok"}],
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": 12,
            "output_tokens": 4,
            "cache_creation_input_tokens": 2,
            "cache_read_input_tokens": 1,
        },
    }
).encode()


def _cached_body(model: str, ttl: str) -> dict[str, Any]:
    return {
        "model": model,
        "max_tokens": 32,
        "system": [
            {"type": "text", "text": "x-anthropic-billing-header: cc_version=2.1.329;"},
            {"type": "text", "text": "sys", "cache_control": {"type": "ephemeral", "ttl": ttl}},
        ],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello", "cache_control": {"type": "ephemeral", "ttl": ttl}},
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_1",
                        "content": [
                            {"type": "text", "text": "nested", "cache_control": {"type": "ephemeral", "ttl": ttl}}
                        ],
                    },
                ],
            }
        ],
    }


def _all_ttls(body: dict[str, Any]) -> list[str]:
    return [
        body["system"][1]["cache_control"]["ttl"],
        body["messages"][0]["content"][0]["cache_control"]["ttl"],
        body["messages"][0]["content"][1]["content"][0]["cache_control"]["ttl"],
    ]


@pytest.mark.e2e
def test_anthropic_cache_tier_policy_over_http(tmp_path):
    if not os.environ.get("PG_CLUSTER_CONF_ROOT"):
        pytest.skip("Run with pg_virtualenv for a disposable PostgreSQL database")

    captured: list[tuple[dict[str, str], dict[str, Any]]] = []

    class _UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            captured.append(
                (
                    {k.lower(): v for k, v in self.headers.items()},
                    json.loads(raw.decode("utf-8")),
                )
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(UPSTREAM_RESPONSE)))
            self.end_headers()
            self.wfile.write(UPSTREAM_RESPONSE)

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
    upstream_port = upstream.server_address[1]
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()

    key_file = tmp_path / "key"
    key = Fernet.generate_key()
    key_file.write_bytes(key)
    key_file.chmod(0o600)
    fernet = Fernet(key)

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"

    env = {
        **os.environ,
        "AGENT_LB_DATA_DIR": str(tmp_path),
        "AGENT_LB_ENCRYPTION_KEY_FILE": str(key_file),
        "AGENT_LB_DATABASE_URL": (
            f"postgresql+asyncpg://{os.environ['PGUSER']}:{os.environ['PGPASSWORD']}"
            f"@127.0.0.1:{os.environ['PGPORT']}/{os.environ['PGDATABASE']}"
        ),
        "AGENT_LB_BASE_URL": base_url,
        "AGENT_LB_ANTHROPIC_UPSTREAM_BASE_URL": f"http://127.0.0.1:{upstream_port}",
        "AGENT_LB_USAGE_REFRESH_ENABLED": "false",
        "AGENT_LB_MODEL_REGISTRY_ENABLED": "false",
    }

    now = datetime.now(timezone.utc)
    try:
        with (tmp_path / "agent-lb.log").open("w") as log:
            with subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
                env=env,
                stdout=log,
                stderr=log,
            ) as server:
                try:
                    with httpx.Client(base_url=base_url, timeout=10, trust_env=False) as client:
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
                            db.execute(
                                """INSERT INTO accounts
                                   (id, email, provider, plan_type, access_token_encrypted,
                                    refresh_token_encrypted, last_refresh, status)
                                   VALUES (%s, %s, 'anthropic', 'max', %s, %s, %s, 'active')""",
                                (
                                    "acct-cache-policy",
                                    "cache-policy@example.invalid",
                                    fernet.encrypt(b"access-cache-policy"),
                                    fernet.encrypt(b"refresh-cache-policy"),
                                    now + timedelta(days=1),
                                ),
                            )

                        fable_delegated = client.post(
                            "/v1/messages",
                            json=_cached_body("claude-fable-5-2", "5m"),
                            headers={
                                "user-agent": T3_UA,
                                "anthropic-beta": "oauth-2025-04-20",
                                "x-agent-lb-thread": DELEGATED_THREAD,
                                "x-agent-lb-seat": "t3-code",
                            },
                        )
                        assert fable_delegated.status_code == 200, fable_delegated.text
                        headers, body = captured[-1]
                        assert _all_ttls(body) == ["1h", "1h", "1h"]
                        assert "extended-cache-ttl-2025-04-11" in headers.get("anthropic-beta", "")

                        fable_top = client.post(
                            "/v1/messages",
                            json=_cached_body("claude-fable-5-2", "5m"),
                            headers={
                                "user-agent": T3_UA,
                                "anthropic-beta": "oauth-2025-04-20",
                                "x-agent-lb-thread": TOP_LEVEL_THREAD,
                                "x-agent-lb-seat": "t3-code",
                            },
                        )
                        assert fable_top.status_code == 200, fable_top.text
                        headers, body = captured[-1]
                        assert _all_ttls(body) == ["1h", "1h", "1h"]
                        assert "extended-cache-ttl-2025-04-11" in headers.get("anthropic-beta", "")

                        recruiter_opus = client.post(
                            "/v1/messages",
                            json=_cached_body("claude-opus-5-5", "5m"),
                            headers={
                                "user-agent": T3_UA,
                                "anthropic-beta": "oauth-2025-04-20",
                                "x-agent-lb-seat": "shared-recruiter",
                                "x-agent-lb-agent": "recruiter",
                            },
                        )
                        assert recruiter_opus.status_code == 200, recruiter_opus.text
                        headers, body = captured[-1]
                        assert _all_ttls(body) == ["1h", "1h", "1h"]
                        assert "extended-cache-ttl-2025-04-11" in headers.get("anthropic-beta", "")
                        assert "x-agent-lb-agent" not in headers

                        for model in ("claude-fable-5-2", "claude-opus-5-5"):
                            sdk_resp = client.post(
                                "/v1/messages",
                                json=_cached_body(model, "1h"),
                                headers={
                                    "user-agent": SDK_CLI_UA,
                                    "anthropic-beta": "oauth-2025-04-20",
                                    "x-agent-lb-seat": "t3-code",
                                },
                            )
                            assert sdk_resp.status_code == 200, sdk_resp.text
                            headers, body = captured[-1]
                            assert _all_ttls(body) == ["5m", "5m", "5m"]
                            assert "extended-cache-ttl-2025-04-11" not in headers.get("anthropic-beta", "")

                            runner_resp = client.post(
                                "/v1/messages",
                                json=_cached_body(model, "1h"),
                                headers={
                                    "user-agent": T3_UA,
                                    "anthropic-beta": "oauth-2025-04-20",
                                    "x-agent-lb-seat": "rails-runner",
                                },
                            )
                            assert runner_resp.status_code == 200, runner_resp.text
                            headers, body = captured[-1]
                            assert _all_ttls(body) == ["5m", "5m", "5m"]
                            assert "extended-cache-ttl-2025-04-11" not in headers.get("anthropic-beta", "")

                        with psycopg.connect(autocommit=True) as db:
                            rows = db.execute(
                                """SELECT model, caller_seat, thread_id, cache_creation_tier
                                   FROM request_logs
                                   ORDER BY id ASC"""
                            ).fetchall()
                        assert rows == [
                            ("claude-fable-5-2", "t3-code", DELEGATED_THREAD, "1h"),
                            ("claude-fable-5-2", "t3-code", TOP_LEVEL_THREAD, "1h"),
                            ("claude-opus-5-5", "shared-recruiter", None, "1h"),
                            ("claude-fable-5-2", "t3-code", None, "5m"),
                            ("claude-fable-5-2", "rails-runner", None, "5m"),
                            ("claude-opus-5-5", "t3-code", None, "5m"),
                            ("claude-opus-5-5", "rails-runner", None, "5m"),
                        ]
                finally:
                    server.terminate()
                    server.wait(timeout=15)
    finally:
        upstream.shutdown()
        upstream.server_close()
