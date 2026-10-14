from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "clients" / "route"


@contextmanager
def server():
    state = {"offline": False, "delay": 0.0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(state["delay"])
            body = {"generatedAt": "2026-10-14T09:00:00Z", "pools": [
                {"id": "anthropic-general", "accounts": 9, "eligibleAccounts": 1,
                 "headroomPercent": 78, "status": "ok"}]}
            status = 503 if state["offline"] else 200
            if self.path != "/api/pools":
                body = {"accounts": []}
            self.send_response(status)
            self.end_headers()
            try:
                self.wfile.write(json.dumps(body).encode())
            except BrokenPipeError:
                pass

        def log_message(self, *args):
            pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}", state
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


def run(tmp_path, url):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("ROUTE_", "AGENT_LB_"))}
    env.update(HOME=str(tmp_path), AGENT_LB_URL=url,
               ROUTE_POOL_SNAPSHOT=str(tmp_path / "snapshot.json"))
    return subprocess.run([sys.executable, str(SCRIPT), "pools", "--json"],
                          env=env, text=True, capture_output=True, timeout=15)


def test_outage_uses_only_fresh_same_origin_snapshot(tmp_path):
    with server() as (url, state):
        initial = run(tmp_path, url)
        assert initial.returncode == 0, initial.stderr
        state["offline"] = True
        cached = run(tmp_path, url)
        assert cached.returncode == 0, cached.stderr
        document = json.loads(cached.stdout)
        assert document["source"] == "cache"
        assert document["generatedAt"] == "2026-10-14T09:00:00Z"
        assert document["pools"][0]["eligibleAccounts"] == 1
        assert 0 <= document["cacheAgeSeconds"] < 60
        snapshot = tmp_path / "snapshot.json"
        record = json.loads(snapshot.read_text())
        record["savedAt"] = time.time() - 61
        snapshot.write_text(json.dumps(record))
        assert run(tmp_path, url).returncode != 0
        record["savedAt"] = time.time()
        record["baseUrl"] = "http://another-origin.invalid"
        snapshot.write_text(json.dumps(record))
        assert run(tmp_path, url).returncode != 0


def test_pool_and_accounts_share_one_timeout_budget(tmp_path):
    with server() as (url, state):
        state.update(offline=True, delay=5.5)
        started = time.monotonic()
        result = run(tmp_path, url)
        elapsed = time.monotonic() - started
        assert result.returncode != 0
        assert elapsed < 6.2, (elapsed, result.stderr)
