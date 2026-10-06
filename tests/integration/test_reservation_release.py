"""A4d-5 (Opus-written 2026-10-06, from the codex review of a4d-4 at cabb23d8): a reservation hold is released only once
nothing of its run is alive, and a hold route cannot account for is never reported gone.

1. A heartbeat under the hold that times out or fails (HTTP 500) says nothing about the hold: route releases it before
   it walks on, so the next reserve is not handed the skipped rung back and the capacity is not stranded.
2. A stop during the model listing under the hold releases only once a listing descendant that ignores SIGTERM is gone.
3. A process group left holding only zombies (its reaper never reaps them) counts as drained: the run ends and the
   lease is released instead of renewing forever.
4. A stop retries a failed lookup or release, and says "error", not "cancelled", while the hold may still be live.

Route runs in its own session, as seat runs it. Fakes: the vendor CLI and model listing, and for 1 and 4 an agent-lb
stand-in whose reservation endpoints fail on cue."""

from __future__ import annotations

import http.server
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from tests.integration.test_route_reservations import route, server  # noqa: F401  (module-scoped server fixture)
from tests.integration.test_seat_run_reservation import (  # noqa: F401  (env is a fixture)
    HOLDERS, ROUTE, _script, env, invocations, mine, pid_alive, reservations, seat_argv, slow_discovery)

pytestmark = pytest.mark.timeout(600)


class FakeLB:
    """agent-lb's reservation endpoints, handing a job its live hold back as agent-lb does, failing on cue: the first
    `list_failures` listings and `release_failures` releases answer 500, and heartbeats answer 500 ("500") or only
    after route's read timeout ("slow")."""

    def __init__(self, *, heartbeat: str = "ok", list_failures: int = 0, release_failures: int = 0) -> None:
        self.holds: dict[str, dict] = {}
        self.released: list[tuple[str, str]] = []
        self.list_failures = list_failures
        self.release_failures = release_failures
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
                if outer.list_failures > 0:
                    outer.list_failures -= 1
                    return self.reply(500, {"detail": "store unavailable"})
                self.reply(200, {"live": list(outer.holds.values()), "recent": []})

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                rid = self.path.rstrip("/").split("/")[-2] if self.path.count("/") > 4 else None
                live = next((row for row in outer.holds.values() if row["reservation_id"] == rid), None)
                if self.path.endswith("/release"):
                    if outer.release_failures > 0:
                        outer.release_failures -= 1
                        return self.reply(500, {"detail": "store unavailable"})
                    if live is None:
                        return self.reply(404, {"status": "gone", "reservation_id": rid})
                    del outer.holds[live["job"]]
                    outer.released.append((rid, body.get("outcome")))
                    return self.reply(200, {**live, "status": "released"})
                if self.path.endswith("/heartbeat"):
                    if heartbeat == "500":
                        return self.reply(500, {"detail": "store unavailable"})
                    if heartbeat == "slow":
                        time.sleep(7)
                    if live is None:
                        return self.reply(410, {"status": "gone", "reservation_id": rid})
                    return self.reply(200, {**live, "status": "reserved"})
                row = outer.holds.get(body["job"]) or {
                    **body["candidates"][0], "reservation_id": f"rsv-{len(outer.released) + len(outer.holds):012d}",
                    "job": body["job"], "expires_at": "2099-01-01T00:00:00Z"}
                outer.holds[body["job"]] = row
                self.reply(200, {**row, "status": "reserved"})

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def reserve_argv(job: str) -> list[str]:
    return [sys.executable, str(ROUTE), "reserve", "mechanical", "--job", job, "--prefer", "cursor-seat",
            "--model", "grok-latest-low", "--reason", "a4d-5", "--json"]


def stop_route_during_listing(env: dict[str, str], job: str, marker: Path) -> tuple[int, dict]:
    """Start `route reserve` in its own session, wait until its listing under the hold has started (marker exists),
    stop the whole group as seat does, and return route's exit code and last JSON line."""
    process = subprocess.Popen(reserve_argv(job), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                               start_new_session=True)
    HOLDERS.append(process)
    deadline = time.monotonic() + 60
    while not marker.exists():
        if process.poll() is not None or time.monotonic() > deadline:
            out, err = process.communicate()
            pytest.fail(f"route never listed models under the hold (rc {process.returncode}):\n{out}\n{err}")
        time.sleep(0.1)
    os.killpg(process.pid, signal.SIGTERM)
    out, err = process.communicate(timeout=60)
    try:
        return process.returncode, json.loads(out.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        pytest.fail(f"route printed no JSON (rc {process.returncode}):\n{out}\n{err}")


@pytest.mark.parametrize("heartbeat", ["500", "slow"])
def test_a_failed_heartbeat_under_the_hold_releases_it_before_walking_on(env: dict[str, str], tmp_path: Path,
                                                                        heartbeat: str) -> None:
    lb = FakeLB(heartbeat=heartbeat)
    try:
        env = {**slow_discovery(env, tmp_path, 0), "AGENT_LB_URL": lb.url}
        result = subprocess.run(reserve_argv("beat-failed"), capture_output=True, text=True, timeout=90, env=env,
                                check=False)
        body = json.loads(result.stdout.strip().splitlines()[-1])
        # The first hold (rsv-...0) is given up, released "failed", and never reported back as this job's reservation.
        assert ("rsv-000000000000", "failed") in lb.released, (lb.released, body)
        assert body.get("reservation_id") != "rsv-000000000000", body
        assert "rsv-000000000000" not in [row["reservation_id"] for row in lb.holds.values()], lb.holds
    finally:
        lb.server.shutdown()


# A model listing that leaves a SIGTERM-ignoring descendant (holding none of the listing's pipes) and lists slowly.
STUBBORN_DISCOVERY = """#!/usr/bin/env python3
import subprocess, sys, time
# The descendant writes its pid only once SIGTERM is ignored, so the stop cannot land before.
subprocess.Popen([sys.executable, "-c", "import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                  "open(os.environ['DISCOVERY_CHILD'], 'w').write(str(os.getpid())); time.sleep(120)"],
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(20)
print("grok-4.7-low - Grok 4.7 Low")
"""


def test_a_stop_during_the_listing_releases_only_once_a_stubborn_descendant_is_gone(env: dict[str, str],
                                                                                   tmp_path: Path) -> None:
    env = {**slow_discovery(env, tmp_path, 0), "DISCOVERY_CHILD": str(tmp_path / "discovery.child"),
           "ROUTE_CURSOR_MODELS_CMD": f"{sys.executable} {_script(tmp_path / 'stubborn', STUBBORN_DISCOVERY)}"}
    child = tmp_path / "discovery.child"
    try:
        rc, body = stop_route_during_listing(env, "stubborn-listing", child)
        released = not [row for row in reservations(env)["live"] if row["job"] == "stubborn-listing"]
        alive = pid_alive(int(child.read_text()))
        assert not (released and alive), "the hold was released while the listing's descendant still ran"
        assert (rc, body["status"], released, alive) == (128 + signal.SIGTERM, "cancelled", True, False), body
    finally:
        if child.exists():
            try:
                os.kill(int(child.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize("failures", [1, 3])
def test_a_stop_retries_a_failed_lookup_and_release(env: dict[str, str], tmp_path: Path, failures: int) -> None:
    lb = FakeLB(list_failures=failures, release_failures=failures)
    try:
        env = {**slow_discovery(env, tmp_path, 20), "AGENT_LB_URL": lb.url}
        rc, body = stop_route_during_listing(env, "flaky-cancel", tmp_path / "discovery.pid")
        assert (rc, body["status"]) == (128 + signal.SIGTERM, "cancelled"), body
        assert lb.holds == {} and [outcome for _rid, outcome in lb.released] == ["cancelled"], (lb.holds, lb.released)
    finally:
        lb.server.shutdown()


def test_a_stop_whose_release_keeps_failing_is_not_reported_cancelled(env: dict[str, str], tmp_path: Path) -> None:
    lb = FakeLB(release_failures=1000)
    try:
        env = {**slow_discovery(env, tmp_path, 20), "AGENT_LB_URL": lb.url}
        rc, body = stop_route_during_listing(env, "stuck-cancel", tmp_path / "discovery.pid")
        assert len(lb.holds) == 1, lb.holds
        assert rc == 128 + signal.SIGTERM and body["status"] == "error", body
        assert "may still be live" in body["reason"], body
    finally:
        lb.server.shutdown()


# A vendor CLI whose child exits at once and is never reaped by its parent, so once the CLI exits the child is a
# zombie handed to the nearest subreaper.
ZOMBIE_CURSOR = """#!/usr/bin/env python3
import json, os, sys, time
if sys.argv[1:] == ["models"]:
    print("Available models")
    raise SystemExit(0)
prompt = sys.stdin.read()
with open(os.environ["FAKE_INVOCATIONS"], "a") as log:
    log.write(prompt.strip() + "\\n")
if os.fork() == 0:
    os._exit(0)
time.sleep(0.5)
print(json.dumps({"type": "result", "is_error": False, "result": "done", "session_id": "chat-1",
                  "usage": {"inputTokens": 10, "outputTokens": 2, "cacheReadTokens": 0}}))
"""

# Runs seat under a subreaper that reaps only seat, as a supervisor that never reaps orphans would.
NON_REAPING_SUBREAPER = """import ctypes, subprocess, sys
if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
    raise SystemExit("prctl failed")
seat = subprocess.Popen(sys.argv[1:])
raise SystemExit(seat.wait())
"""


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the non-reaping subreaper uses Linux prctl")
def test_a_group_of_zombies_is_drained_and_the_lease_released(env: dict[str, str], tmp_path: Path) -> None:
    env = {**env, "SEAT_CURSOR_BIN": str(_script(tmp_path / "zombie-cursor", ZOMBIE_CURSOR))}
    launcher = tmp_path / "subreaper.py"
    launcher.write_text(NON_REAPING_SUBREAPER)
    process = subprocess.Popen([sys.executable, str(launcher), *seat_argv(env, "zombie1", "--class", "mechanical")],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                               start_new_session=True)
    try:
        out, err = process.communicate(timeout=45)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        out, err = process.communicate()
        pytest.fail(f"seat never finished draining a group of zombies:\n{err[-2000:]}")
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
    assert process.returncode == 0, out + err
    run = json.loads(out)
    assert run["ok"] and "zombie1" in invocations(env), run
    assert mine(reservations(env)["live"]) == []
    [ended] = [row for row in reservations(env)["recent"] if row["job"] == f"seat/host-a/{run['run_id']}"]
    assert ended["outcome"] == "ok", ended
