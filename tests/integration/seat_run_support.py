from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator

import pytest

from tests.integration.route_support import SCRIPT as ROUTE
from tests.integration.route_support import setup, table_copy
from tests.integration.test_route_reservations import route  # noqa: F401  (module-scoped server fixture)

REPO = Path(__file__).resolve().parents[2]

SEAT = REPO / "clients" / "seat"

CONCURRENCY = {
    "per_account": {"cursor": 1, "devin": 1, "openai": 3, "anthropic": 4},
    "default": 2,
    "ttl_s": 4,
    "heartbeat_s": 1,
}

FAKE_CURSOR = """#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
if sys.argv[1:] == ["models"]:
    with open(os.environ["FAKE_INVOCATIONS"], "a") as log:
        log.write("models\\n")
    if os.environ.get("FAKE_MODELS_SLEEP"):
        time.sleep(float(os.environ["FAKE_MODELS_SLEEP"]))
        print("composer-2.5 - Composer 2.5")
        raise SystemExit(0)
    print("Available models")
    raise SystemExit(0)
prompt = sys.stdin.read()
with open(os.environ["FAKE_INVOCATIONS"], "a") as log:
    log.write(prompt.strip() + "\\n")
(Path(os.environ["FAKE_GATE_DIR"]) / (prompt.strip() + ".pid")).write_text(str(os.getpid()))
(Path(os.environ["FAKE_GATE_DIR"]) / (prompt.strip() + ".argv")).write_text(json.dumps(sys.argv[1:]))
if prompt.startswith("orphan"):
    import subprocess
    child = subprocess.Popen([sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, "
                              "signal.SIG_IGN); time.sleep(120)"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (Path(os.environ["FAKE_GATE_DIR"]) / (prompt.strip() + ".child")).write_text(str(child.pid))
gate = Path(os.environ["FAKE_GATE_DIR"]) / prompt.strip()
deadline = time.monotonic() + 120
while prompt.startswith(("hold", "orphan")) and not gate.exists() and time.monotonic() < deadline:
    time.sleep(0.1)
if prompt.startswith("fail"):
    print(json.dumps({"type": "result", "is_error": True, "result": "tests failed"}))
    raise SystemExit(1)
print(json.dumps({"type": "result", "is_error": False, "result": "done", "session_id": "chat-1",
                  "usage": {"inputTokens": 10, "outputTokens": 2, "cacheReadTokens": 0}}))
"""


def _script(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(0o755)
    return path


HOLDERS: list[subprocess.Popen] = []


@pytest.fixture
def env(tmp_path: Path, server: str) -> Iterator[dict[str, str]]:  # noqa: F811
    base = setup(tmp_path, codex_low=False)
    table = table_copy(tmp_path, "capacity", lambda t: t["policy"].update(concurrency=CONCURRENCY))
    (tmp_path / "gates").mkdir()
    base.update(
        AGENT_LB_URL=server,
        ROUTE_TABLE=str(table),
        ROUTE_HOST="host-a",
        ROUTE_BIN=str(ROUTE),
        ROUTE_LEDGER=str(tmp_path / "dispatch.jsonl"),
        SEAT_HOME=str(tmp_path / "seats"),
        SEAT_CURSOR_BIN=str(_script(tmp_path / "cursor-agent", FAKE_CURSOR)),
        FAKE_INVOCATIONS=str(tmp_path / "invocations.log"),
        FAKE_GATE_DIR=str(tmp_path / "gates"),
    )
    key = tmp_path / "a.key"
    key.write_text("key-a\n")
    key.chmod(0o600)
    added = subprocess.run(
        [sys.executable, str(SEAT), "add", "cursor-a", "--vendor", "cursor", "--api-key-file", str(key)],
        capture_output=True,
        text=True,
        timeout=60,
        env=base,
        check=False,
    )
    assert added.returncode == 0, added.stderr
    yield base

    while HOLDERS:
        holder = HOLDERS.pop()
        if holder.poll() is None:
            holder.terminate()
            try:
                holder.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                holder.kill()
                holder.communicate()


MODEL = "grok-latest-low"


def seat_argv(env: dict[str, str], prompt: str, *extra: str, model: str = MODEL) -> list[str]:
    return [
        sys.executable,
        str(SEAT),
        "run",
        "--vendor",
        "cursor",
        "--model",
        model,
        *extra,
        "--cwd",
        str(Path(env["FAKE_GATE_DIR"]).parent),
        "--",
        prompt,
    ]


def run_seat(env: dict[str, str], prompt: str, *extra: str, model: str = MODEL) -> tuple[int, dict]:
    result = subprocess.run(
        seat_argv(env, prompt, *extra, model=model), capture_output=True, text=True, timeout=120, env=env, check=False
    )
    try:
        return result.returncode, json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.fail(f"seat run printed no JSON (rc {result.returncode}):\n{result.stdout}\n{result.stderr}")


def start_seat(env: dict[str, str], prompt: str) -> subprocess.Popen:
    process = subprocess.Popen(
        seat_argv(env, prompt, "--class", "mechanical"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    HOLDERS.append(process)
    deadline = time.monotonic() + 90
    while prompt not in invocations(env):
        if process.poll() is not None or time.monotonic() > deadline:
            process.kill()
            out, err = process.communicate()
            pytest.fail(f"seat run never reached the vendor CLI (rc {process.returncode}):\n{out}\n{err}")
        time.sleep(0.1)
    return process


def invocations(env: dict[str, str]) -> list[str]:
    path = Path(env["FAKE_INVOCATIONS"])
    return path.read_text().split() if path.exists() else []


def open_gate(env: dict[str, str], prompt: str) -> None:
    (Path(env["FAKE_GATE_DIR"]) / prompt).write_text("go")


def reservations(env: dict[str, str]) -> dict:
    rc, body = route(env, "reservations")
    assert rc == 0, body
    return body


def mine(rows: list[dict]) -> list[dict]:
    return [row for row in rows if row["job"].startswith("seat/host-a/")]


def ledger(env: dict[str, str], event: str) -> list[dict]:
    rows = [json.loads(line) for line in Path(env["ROUTE_LEDGER"]).read_text().splitlines()]
    return [row for row in rows if row.get("event") == event and row.get("source") == "seat"]


def vendor_alive(env: dict[str, str], prompt: str, suffix: str = "pid") -> bool:
    pid = int((Path(env["FAKE_GATE_DIR"]) / f"{prompt}.{suffix}").read_text())
    return pid_alive(pid)


def pid_alive(pid: int) -> bool:

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False)
    return not state.stdout.strip().startswith("Z")


def cursor_pool(env: dict[str, str], **fields: object) -> None:
    path = Path(env["ROUTE_FIXTURE_DIR"]) / "api_pools.json"
    document = json.loads(path.read_text())
    for pool in document["pools"]:
        if pool["id"] == "cursor-models":
            pool.update(fields)
    path.write_text(json.dumps(document))


def cold_cursor_cache(env: dict[str, str]) -> None:
    cache = Path(env["ROUTE_MODELS_CACHE"]).with_name("route-cursor-models.json")
    cache.unlink(missing_ok=True)
    cache.with_suffix(".failed.json").unlink(missing_ok=True)


def launched_model(env: dict[str, str], prompt: str) -> str:
    argv = json.loads((Path(env["FAKE_GATE_DIR"]) / f"{prompt}.argv").read_text())
    return argv[argv.index("--model") + 1]


DISCOVERY = """#!/usr/bin/env python3
import json, os, urllib.request
with urllib.request.urlopen(os.environ["AGENT_LB_URL"] + "/api/pools/reservations", timeout=10) as response:
    live = [row for row in json.load(response)["live"] if row["job"].startswith("seat/host-a/")]
with open(os.environ["DISCOVERY_LOG"], "a") as log:
    log.write(f"live={len(live)}\\n")
print("grok-4.7-medium - Grok 4.7 Medium")
print("grok-4.7-low - Grok 4.7 Low")
print("composer-2.5 - Composer 2.5")
print("claude-sonnet-5-5-high - Sonnet 5.5 High")
"""

SCRIPTED_ROUTE = """#!/bin/sh
echo "$*" >> "$ROUTE_CALLS"
case "$1" in
  reserve)
    [ -n "$SIGNAL_ON_RESERVE" ] && kill -"$SIGNAL_ON_RESERVE" "$PPID"
    echo '{"status": "reserved", "reservation_id": "rsv-000000000009", "rung": "grok-low", "model": "grok-4.7-low",'\
      '"alias": "grok-latest-low", "heartbeat_s": 300}' ;;
  release)
    [ -n "$SIGNAL_ON_RELEASE" ] && kill -"$SIGNAL_ON_RELEASE" "$PPID" && sleep 1
    if [ -n "$FAIL_FIRST_RELEASE" ] && [ ! -e "$ROUTE_CALLS.failed" ]; then
      : > "$ROUTE_CALLS.failed"; echo '{"status": "error"}'; exit 1
    fi
    echo "released $4" >> "$ROUTE_CALLS"
    echo '{"status": "released"}' ;;
  heartbeat) echo '{"status": "live"}' ;;
  resolve)
    [ -n "$SIGNAL_ON_RESOLVE" ] && kill -"$SIGNAL_ON_RESOLVE" "$PPID" && sleep 30
    case "$2" in grok-latest-low) echo grok-4.7-low ;; *) echo "$2" ;; esac ;;
esac
"""

NO_THREADS = """import runpy, sys, threading
def refuse(self):
    raise RuntimeError("can't start new thread")
threading.Thread.start = refuse
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""


@pytest.fixture
def scripted(tmp_path: Path) -> dict[str, str]:
    seats = tmp_path / "seats"
    seats.mkdir()
    (seats / "accounts.json").write_text(
        json.dumps({"accounts": [{"id": "fixture", "vendor": "cursor", "auth": "login"}]})
    )
    (tmp_path / "gates").mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ROUTE_", "SEAT_", "AGENT_LB_", "CURSOR_"))}
    env.update(
        HOME=str(tmp_path),
        SEAT_HOME=str(seats),
        ROUTE_LEDGER=str(tmp_path / "ledger"),
        ROUTE_HOST="host-a",
        ROUTE_BIN=str(_script(tmp_path / "route", SCRIPTED_ROUTE)),
        ROUTE_CALLS=str(tmp_path / "calls"),
        SEAT_CURSOR_BIN=str(_script(tmp_path / "cursor-agent", FAKE_CURSOR)),
        FAKE_INVOCATIONS=str(tmp_path / "invocations.log"),
        FAKE_GATE_DIR=str(tmp_path / "gates"),
    )
    return env


def route_calls(env: dict[str, str]) -> list[str]:
    path = Path(env["ROUTE_CALLS"])
    return path.read_text().splitlines() if path.exists() else []


STOP_BEFORE_FIRST_ATTEMPT = """import os, signal, sys
path = sys.argv[1]
sys.argv = sys.argv[1:]
seat = {"__name__": "seat_under_test", "__file__": path}
exec(compile(open(path).read(), path, "exec"), seat)
build = seat["build_command"]
def build_then_stop(*args):
    os.kill(os.getpid(), signal.SIGTERM)
    return build(*args)
seat["build_command"] = build_then_stop
raise SystemExit(seat["main"]())
"""

SLOW_DISCOVERY = """#!/usr/bin/env python3
import os, time
from pathlib import Path
Path(os.environ["DISCOVERY_PID"]).write_text(str(os.getpid()))
time.sleep(float(os.environ["ROUTE_TEST_LIST_SLEEP"]))
print("grok-4.7-low - Grok 4.7 Low")
print("composer-2.5 - Composer 2.5")
"""


def slow_discovery(env: dict[str, str], tmp_path: Path, seconds: float) -> dict[str, str]:
    cold_cursor_cache(env)
    return {
        **env,
        "DISCOVERY_PID": str(tmp_path / "discovery.pid"),
        "ROUTE_TEST_LIST_SLEEP": str(seconds),
        "ROUTE_CURSOR_MODELS_CMD": f"{sys.executable} {_script(tmp_path / 'slow-discovery', SLOW_DISCOVERY)}",
    }


UNKILLABLE = """import os, sys
path = sys.argv[1]
sys.argv = sys.argv[1:]
seat = {"__name__": "seat_under_test", "__file__": path}
exec(compile(open(path).read(), path, "exec"), seat)
alive = seat["group_alive"]
seat["group_alive"] = lambda pgid: os.path.exists(os.environ["SURVIVOR"]) or alive(pgid)
seat["STOP_GRACE_SECONDS"] = 0.5
raise SystemExit(seat["main"]())
"""


class FailingReleaseLB:
    def __init__(self) -> None:
        import http.server
        import threading

        holds: dict[str, dict] = {}
        self.releases = 0
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def reply(self, code: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                self.reply(200, {"live": list(holds.values()), "recent": []})

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                if self.path.endswith("/release"):
                    outer.releases += 1
                    self.reply(500, {"detail": "store unavailable"})
                elif self.path.endswith("/heartbeat"):
                    self.reply(200, {**next(iter(holds.values())), "status": "reserved"})
                else:
                    row = holds.get(body["job"]) or {
                        **body["candidates"][0],
                        "reservation_id": "rsv-00000000000a",
                        "job": body["job"],
                        "expires_at": "2099-01-01T00:00:00Z",
                    }
                    holds[body["job"]] = row
                    self.reply(200, {**row, "status": "reserved"})

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
