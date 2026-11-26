from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

REPO = Path(__file__).resolve().parents[2]


class EdgeHealth(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp, HTTPServer(("127.0.0.1", 0), EdgeHealth) as edge:
        root = Path(tmp)
        calls = root / "systemd-calls"
        systemctl = root / "systemctl"
        systemctl.write_text(
            "#!/bin/sh\n"
            'printf "%s\\n" "$*" >> "$SYSTEMD_CHECK_CALLS"\n'
            'case "$*" in\n'
            '  "--user is-active agent-lb-standby") printf "activating\\n" ;;\n'
            '  "--user restart agent-lb-edge.service agent-lb-model-door.service") exit 0 ;;\n'
            "esac\n"
            "exit 1\n"
        )
        systemctl.chmod(0o755)
        systemd_run = root / "systemd-run"
        systemd_run.write_text('#!/bin/sh\nprintf "systemd-run\\n" >> "$SYSTEMD_CHECK_CALLS"\nexit 1\n')
        systemd_run.chmod(0o755)

        server = Thread(target=edge.serve_forever, daemon=True)
        server.start()
        try:
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from scripts.systemd_handoff import restart\n"
                    "from scripts.runtime_tree import Paths\n"
                    "rc, details = restart(['agent-lb-edge.service', 'agent-lb-model-door.service'], Paths.from_env())\n"
                    "assert rc == 0, details\n",
                ],
                check=True,
                cwd=REPO,
                env={
                    **os.environ,
                    "PATH": f"{root}{os.pathsep}{os.environ['PATH']}",
                    "SYSTEMD_CHECK_CALLS": str(calls),
                    "AGENT_LB_FOLLOW_ROOT": str(root / "edge" / "follow"),
                    "AGENT_LB_FOLLOW_HEALTH_URL": f"http://127.0.0.1:{edge.server_port}/health",
                },
                timeout=35,
            )
        finally:
            edge.shutdown()
            server.join()
        actual = calls.read_text().splitlines()
        expected = ["--user restart agent-lb-edge.service agent-lb-model-door.service"]
        assert actual == expected, f"Edge restart touched the primary standby: {actual}"
    print("Edge and model door restarted without touching the primary standby")


if __name__ == "__main__":
    main()
