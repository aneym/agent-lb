"""`agent-lb status` under load (2026-10-01): orch-watch and routing read it while Studio sits at load 300-480.

/health answered in 30 ms but /api/accounts took over 3 s, so status gave up ("service is
unavailable or did not respond") and every reader saw no accounts. Status now waits longer by
default, and when the service still can't answer it serves its last good answer, marked stale,
for up to 10 minutes.

These drive the real CLI against a local stand-in service; only the service's speed is faked.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app import cli

pytestmark = pytest.mark.unit

ACCOUNT = {
    "accountId": "acc-load-1",
    "provider": "openai",
    "email": "secret@example.test",
    "displayName": "Secret Operator",
    "status": "active",
    "subscription": {"status": "active"},
    "usage": {"primaryRemainingPercent": 40, "secondaryRemainingPercent": 70},
    "additionalQuotas": [],
}


class _Service:
    def __init__(self, accounts_delay: float = 0.0) -> None:
        delay = accounts_delay

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/health/ready":
                    code, body = 200, {"status": "ok"}
                elif self.path == "/api/accounts":
                    time.sleep(delay)
                    code, body = 200, {"accounts": [ACCOUNT]}
                else:
                    code, body = 404, {}
                encoded = json.dumps(body).encode()
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                except OSError:
                    pass

            def log_message(self, *_args) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()


@pytest.fixture(autouse=True)
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "status-cache"
    monkeypatch.setenv("AGENT_LB_STATUS_CACHE_DIR", str(path))
    monkeypatch.delenv("AGENT_LB_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_LB_STATUS_COOKIE", raising=False)
    return path


def _status(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict]:
    code = 0
    try:
        cli.main(["status", "--json", *args])
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    out = capsys.readouterr().out
    assert "secret@example.test" not in out and "Secret Operator" not in out
    return code, json.loads(out)


def test_status_waits_out_a_slow_accounts_endpoint(capsys: pytest.CaptureFixture[str]) -> None:
    service = _Service(accounts_delay=4.5)
    try:
        code, payload = _status(capsys, "--base-url", service.url, "--provider", "openai")
    finally:
        service.stop()
    assert code == 0
    assert payload["health"]["state"] == "ready"
    assert [account["account_id"] for account in payload["accounts"]] == ["acc-load-1"]
    assert not payload.get("stale")


def test_a_service_that_stops_answering_gets_the_last_good_answer_marked_stale(
    capsys: pytest.CaptureFixture[str], cache_dir: Path
) -> None:
    service = _Service()
    url = service.url
    code, fresh = _status(capsys, "--base-url", url, "--provider", "openai")
    assert code == 0 and not fresh.get("stale")
    service.stop()  # the port now refuses connections

    code, served = _status(capsys, "--base-url", url, "--provider", "openai", "--timeout", "1")
    assert code == 0
    assert served["stale"] is True
    assert served["accounts"] == fresh["accounts"]
    assert served["providers"] == fresh["providers"]
    assert served["cached_at"] == fresh["observed_at"]
    assert served["error"]["kind"] == "observation_failed"
    assert 0 <= served["cache_age_seconds"] < 60
    # The cache never holds an email or display name.
    for path in cache_dir.rglob("*"):
        if path.is_file():
            text = path.read_text()
            assert "secret@example.test" not in text and "Secret Operator" not in text


def test_a_cache_older_than_ten_minutes_is_not_served(capsys: pytest.CaptureFixture[str], cache_dir: Path) -> None:
    service = _Service()
    url = service.url
    code, _ = _status(capsys, "--base-url", url)
    assert code == 0
    service.stop()
    old = time.time() - 11 * 60
    files = [path for path in cache_dir.rglob("*") if path.is_file()]
    assert files
    for path in files:
        os.utime(path, (old, old))  # a cache entry's age is its file's mtime
    code, payload = _status(capsys, "--base-url", url, "--timeout", "1")
    assert code == 2
    assert payload["health"]["state"] == "error"
    assert payload["accounts"] == []
