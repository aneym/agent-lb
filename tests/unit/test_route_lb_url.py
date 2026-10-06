"""A factory box has no agent-lb on 127.0.0.1:2455 (ax42, 2026-10-06: `route reservations` got "Connection refused",
so `seat run --class` could not reserve). The box's lb-proxy install writes its proxy URL to
~/.config/agent-lb/client.env; route reads it when AGENT_LB_URL is not in the environment, and the environment wins."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "clients" / "route"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = json.dumps({"live": [{"job": "from-client-env"}], "recent": []}).encode()
        self.send_response(200 if self.path == "/api/pools/reservations" else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass


def reservations(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "reservations", "--json"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        check=False,
    )


def test_route_reaches_the_lb_named_in_client_env_unless_the_environment_names_one(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        home = tmp_path / "home"
        (home / ".config" / "agent-lb").mkdir(parents=True)
        (home / ".config" / "agent-lb" / "client.env").write_text(
            f"OTHER=1\nexport AGENT_LB_URL='http://127.0.0.1:{server.server_address[1]}/'\n"
        )
        env = {k: v for k, v in os.environ.items() if not k.startswith(("ROUTE_", "AGENT_LB_"))}
        env.update(HOME=str(home), ROUTE_LEDGER=str(tmp_path / "dispatch.jsonl"))

        result = reservations(env)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["live"] == [{"job": "from-client-env"}]

        result = reservations({**env, "AGENT_LB_URL": "http://127.0.0.1:1"})
        assert result.returncode != 0
        assert json.loads(result.stdout)["error"] == "reservations_unavailable"
    finally:
        server.shutdown()
        server.server_close()
