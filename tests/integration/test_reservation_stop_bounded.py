"""A4d-6 (Opus-written 2026-10-06, from the review of a4d-5 at ede08980): a stopped run ends its processes through
their process group only and answers inside seat's stop grace; no process table is read.

1. On a host without /proc (macOS), route's stop does not list its own scanner as a member of the group it waits on:
   the hold is released and route says "cancelled" inside seat's stop grace.
2. A pid route read from a process table may belong to an unrelated process by the time its SIGKILL lands. Route
   signals only its listing's process group, so an outsider survives a stop even when every pid-addressed SIGKILL
   would hit it.
3. On Linux a thread group whose leader is a zombie can still run threads. Neither route nor seat takes such a
   process for gone: its threads are ended before the hold or lease is released.
4. However slow agent-lb answers, route's stop prints its answer before seat's 10 s grace ends.

Fakes: the model listing and the vendor CLI, an agent-lb stand-in for 4, and for 1 and 2 a launcher that runs the real
clients/route with /proc hidden or with pid-addressed signals redirected to an outsider."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.integration.test_reservation_release import FakeLB, reserve_argv
from tests.integration.test_route_reservations import route, server  # noqa: F401  (module-scoped server fixture)
from tests.integration.test_seat_run_reservation import (  # noqa: F401  (env is a fixture)
    HOLDERS, _script, env, invocations, mine, reservations, seat_argv, slow_discovery)

pytestmark = pytest.mark.timeout(600)

SEAT_STOP_GRACE_S = 10.0

# Runs the real route with one platform condition emulated: "no-proc" hides /proc as on macOS; "pid-reused" sends
# every SIGKILL that route addresses to a single pid to OUTSIDER_PID instead, as if that pid had been reused.
LAUNCHER = """import os, pathlib, runpy, signal, sys
mode = os.environ["ROUTE_TEST_EMULATE"]
if mode == "no-proc":
    real_exists = pathlib.Path.exists
    pathlib.Path.exists = lambda self, *a, **k: False if str(self).startswith("/proc") else real_exists(self, *a, **k)
elif mode == "pid-reused":
    outsider, real_kill = int(os.environ["OUTSIDER_PID"]), os.kill
    os.kill = lambda pid, sig: real_kill(outsider if sig == signal.SIGKILL and pid != os.getpid() else pid, sig)
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""

# A process that ignores SIGTERM, keeps a thread appending to STRAY_BEATS, and ends its main thread, leaving a zombie
# thread-group leader with a live thread. It writes STRAY_READY once in that state is imminent.
STRAY = """import ctypes, os, signal, threading, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
def beat():
    while True:
        with open(os.environ["STRAY_BEATS"], "a") as beats:
            beats.write(".")
        time.sleep(0.05)
threading.Thread(target=beat).start()
time.sleep(0.2)
with open(os.environ["STRAY_READY"], "w") as ready:
    ready.write(str(os.getpid()))
ctypes.CDLL(None).pthread_exit(None)
"""

# A model listing that leaves the stray behind (holding none of its pipes) and lists slowly.
STRAY_DISCOVERY = """#!/usr/bin/env python3
import os, subprocess, sys, time
subprocess.Popen([sys.executable, os.environ["STRAY_SCRIPT"]], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL)
time.sleep(20)
print("grok-4.7-low - Grok 4.7 Low")
"""

# A vendor CLI that leaves the stray in its process group and exits once the stray is in place.
STRAY_CURSOR = """#!/usr/bin/env python3
import json, os, subprocess, sys, time
if sys.argv[1:] == ["models"]:
    print("Available models")
    raise SystemExit(0)
prompt = sys.stdin.read()
with open(os.environ["FAKE_INVOCATIONS"], "a") as log:
    log.write(prompt.strip() + "\\n")
subprocess.Popen([sys.executable, os.environ["STRAY_SCRIPT"]], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL)
while not os.path.exists(os.environ["STRAY_READY"]):
    time.sleep(0.05)
time.sleep(0.3)
print(json.dumps({"type": "result", "is_error": False, "result": "done", "session_id": "chat-1",
                  "usage": {"inputTokens": 10, "outputTokens": 2, "cacheReadTokens": 0}}))
"""


def stop_during_listing(argv: list[str], env: dict[str, str], marker: Path) -> tuple[int, dict, float]:
    """Start route in its own session as seat does, stop its group once `marker` exists, and return route's exit
    code, last JSON line and the seconds from the stop to its exit."""
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                               start_new_session=True)
    HOLDERS.append(process)
    deadline = time.monotonic() + 60
    while not marker.exists():
        if process.poll() is not None or time.monotonic() > deadline:
            out, err = process.communicate()
            pytest.fail(f"route never listed models under the hold (rc {process.returncode}):\n{out}\n{err}")
        time.sleep(0.05)
    os.killpg(process.pid, signal.SIGTERM)
    stopped = time.monotonic()
    out, err = process.communicate(timeout=90)
    elapsed = time.monotonic() - stopped
    try:
        return process.returncode, json.loads(out.strip().splitlines()[-1]), elapsed
    except (IndexError, json.JSONDecodeError):
        pytest.fail(f"route printed no JSON (rc {process.returncode}):\n{out}\n{err}")


def launched(tmp_path: Path, argv: list[str]) -> list[str]:
    launcher = tmp_path / "launcher.py"
    launcher.write_text(LAUNCHER)
    return [argv[0], str(launcher), *argv[1:]]


def held(env: dict[str, str], job: str) -> bool:
    return any(row["job"] == job for row in reservations(env)["live"])


def still_beating(beats: Path) -> bool:
    before = beats.stat().st_size if beats.exists() else 0
    time.sleep(0.6)
    return (beats.stat().st_size if beats.exists() else 0) > before


def stray_env(env: dict[str, str], tmp_path: Path) -> dict[str, str]:
    return {**env, "STRAY_SCRIPT": str(tmp_path / "stray.py"), "STRAY_BEATS": str(tmp_path / "stray.beats"),
            "STRAY_READY": str(tmp_path / "stray.ready")}


def kill_stray(env: dict[str, str]) -> None:
    ready = Path(env["STRAY_READY"])
    if ready.exists():
        try:
            os.kill(int(ready.read_text()), signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_without_proc_a_stop_releases_inside_the_grace(env: dict[str, str], tmp_path: Path) -> None:
    env = {**slow_discovery(env, tmp_path, 20), "ROUTE_TEST_EMULATE": "no-proc"}
    rc, body, elapsed = stop_during_listing(launched(tmp_path, reserve_argv("no-proc")), env,
                                            tmp_path / "discovery.pid")
    assert (rc, body["status"], held(env, "no-proc")) == (128 + signal.SIGTERM, "cancelled", False), body
    assert elapsed < SEAT_STOP_GRACE_S, (elapsed, body)


def test_a_stop_never_signals_a_reused_pid(env: dict[str, str], tmp_path: Path) -> None:
    from tests.integration.test_reservation_release import STUBBORN_DISCOVERY
    outsider = subprocess.Popen(["sleep", "120"], start_new_session=True)
    child = tmp_path / "discovery.child"
    env = {**slow_discovery(env, tmp_path, 0), "DISCOVERY_CHILD": str(child), "ROUTE_TEST_EMULATE": "pid-reused",
           "OUTSIDER_PID": str(outsider.pid),
           "ROUTE_CURSOR_MODELS_CMD": f"{sys.executable} {_script(tmp_path / 'stubborn', STUBBORN_DISCOVERY)}"}
    try:
        rc, body, elapsed = stop_during_listing(launched(tmp_path, reserve_argv("pid-reused")), env, child)
        assert outsider.poll() is None, "route SIGKILLed a pid outside its listing's process group"
        assert (rc, body["status"], held(env, "pid-reused")) == (128 + signal.SIGTERM, "cancelled", False), body
        assert elapsed < SEAT_STOP_GRACE_S, (elapsed, body)
    finally:
        outsider.kill()
        outsider.wait()
        if child.exists():
            try:
                os.kill(int(child.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="a zombie leader with live threads is Linux /proc")
def test_route_releases_only_once_a_zombie_leaders_threads_are_gone(env: dict[str, str], tmp_path: Path) -> None:
    _script(tmp_path / "stray.py", STRAY)
    env = {**stray_env(slow_discovery(env, tmp_path, 0), tmp_path),
           "ROUTE_CURSOR_MODELS_CMD": f"{sys.executable} {_script(tmp_path / 'stray-discovery', STRAY_DISCOVERY)}"}
    try:
        rc, body, elapsed = stop_during_listing(reserve_argv("zombie-leader"), env, Path(env["STRAY_READY"]))
        released, beating = not held(env, "zombie-leader"), still_beating(Path(env["STRAY_BEATS"]))
        assert not (released and beating), "the hold was released while the zombie leader's thread still ran"
        assert (rc, body["status"], released, beating) == (128 + signal.SIGTERM, "cancelled", True, False), body
        assert elapsed < SEAT_STOP_GRACE_S, (elapsed, body)
    finally:
        kill_stray(env)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="a zombie leader with live threads is Linux /proc")
def test_seat_releases_only_once_a_zombie_leaders_threads_are_gone(env: dict[str, str], tmp_path: Path) -> None:
    _script(tmp_path / "stray.py", STRAY)
    env = {**stray_env(env, tmp_path), "SEAT_CURSOR_BIN": str(_script(tmp_path / "stray-cursor", STRAY_CURSOR))}
    try:
        result = subprocess.run(seat_argv(env, "zombie2", "--class", "mechanical"), capture_output=True, text=True,
                                timeout=90, env=env, check=False)
        assert not still_beating(Path(env["STRAY_BEATS"])), "seat ended while the zombie leader's thread still ran"
        assert result.returncode == 0, result.stdout + result.stderr
        run = json.loads(result.stdout)
        assert run["ok"] and "zombie2" in invocations(env), run
        assert mine(reservations(env)["live"]) == []
    finally:
        kill_stray(env)


class SlowLookupLB(FakeLB):
    """FakeLB whose listing of holds answers only after route's read timeout once a hold exists."""

    def __init__(self) -> None:
        super().__init__()
        outer = self
        handler = self.server.RequestHandlerClass
        answer = handler.do_GET

        def do_get(request: object) -> None:
            if outer.holds:
                time.sleep(6)
            answer(request)

        handler.do_GET = do_get


def test_a_stop_answers_inside_the_grace_however_slow_the_lookup(env: dict[str, str], tmp_path: Path) -> None:
    lb = SlowLookupLB()
    try:
        env = {**slow_discovery(env, tmp_path, 20), "AGENT_LB_URL": lb.url}
        rc, body, elapsed = stop_during_listing(reserve_argv("slow-lookup"), env, tmp_path / "discovery.pid")
        assert elapsed < SEAT_STOP_GRACE_S, (elapsed, body)
        assert rc == 128 + signal.SIGTERM and body["status"] == "error", body
        assert "may still be live" in body["reason"], body
    finally:
        lb.server.shutdown()
