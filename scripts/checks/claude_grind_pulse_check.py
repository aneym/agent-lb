#!/usr/bin/env python3
import json
import os
import resource
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOOK = Path(__file__).resolve().parents[2] / "config/coding-agents/hooks/claude-grind-pulse.py"


def main():
    state = {"other": 0, "calls": 0, "broken": False}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path.endswith("/analytics?windowMinutes=60")
            state["calls"] += 1
            self.send_response(200)
            self.end_headers()
            body = {
                "seats": [
                    {"model": "claude-opus-5-5", "requests": 200, "costUsd": 10},
                    {"model": "gpt-6.1-sol", "requests": state["other"], "costUsd": 1},
                ]
            }
            self.wfile.write(b"invalid" if state["broken"] else json.dumps(body).encode())

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            ledger = home / "dispatch.jsonl"
            ledger.write_text("")
            env = dict(
                os.environ,
                HOME=directory,
                DISPATCH_LEDGER=str(ledger),
                AGENT_LB_URL=f"http://127.0.0.1:{server.server_port}",
            )

            def run(session):
                result = subprocess.run(
                    [sys.executable, str(HOOK)],
                    input=json.dumps({"session_id": session}),
                    text=True,
                    capture_output=True,
                    env=env,
                    timeout=4,
                )
                assert result.returncode == 0, result
                assert not result.stderr, result.stderr
                return result.stdout

            fired = json.loads(run("fire"))["hookSpecificOutput"]
            assert fired["hookEventName"] == "PostToolUse"
            text = fired["additionalContext"]
            assert len(text.encode()) <= 600 and "Claude 200" in text and "dispatches 0" in text
            assert "/route-fix" in text and "gpt-implementer / gpt-explorer / codex-sol" in text
            calls = state["calls"]
            before = resource.getrusage(resource.RUSAGE_CHILDREN)
            assert run("fire") == "" and state["calls"] == calls
            after = resource.getrusage(resource.RUSAGE_CHILDREN)
            cpu = (after.ru_utime + after.ru_stime) - (before.ru_utime + before.ru_stime)
            assert cpu < 0.08, f"throttled hook used {cpu:.6f}s CPU (limit 0.08s)"
            print(f"PASS throttled CPU: {cpu:.6f}s < 0.08s")
            print("PASS fire: 200 Claude, zero dispatches; context <= 600 bytes; immediate throttle avoids HTTP")
            checked = home / ".cache/claude-grind-pulse/fire"
            old = (datetime.now(timezone.utc) - timedelta(minutes=11)).timestamp()
            os.utime(checked, (old, old))
            assert run("fire") == "" and state["calls"] == calls
            print("PASS fired marker: suppresses repeat after check throttle expires")
            now = datetime.now(timezone.utc).isoformat()
            ledger.write_text(
                "".join(json.dumps({"event": "dispatch", "session_id": "quiet", "ts": now}) + "\n" for _ in range(5))
            )
            state["other"] = 100
            assert run("quiet") == ""
            print("PASS quiet: 200 Claude, five dispatches, 100 non-Claude")
            state["other"] = 0
            text = json.loads(run("quiet-ratio"))["hookSpecificOutput"]["additionalContext"]
            assert "dispatches 0" in text
            ledger.write_text(ledger.read_text().replace("quiet", "low-ratio"))
            assert "dispatches 5" in json.loads(run("low-ratio"))["hookSpecificOutput"]["additionalContext"]
            print("PASS ratio: dispatching without non-Claude throughput still warns")
            state["broken"] = True
            assert run("broken") == ""
            env["AGENT_LB_URL"] = "http://127.0.0.1:1"
            assert run("offline") == ""
            print("PASS analytics errors and LB offline: silent success")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
