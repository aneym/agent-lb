"""A4d scenario (Opus-written 2026-10-06; the implementer may not edit this file). The devin-seat and cursor-seat
forwarders call `seat run --class <class>` directly, never factory seat-run, so A4a's reservations do nothing for them
unless `seat run` takes one itself (complaint 2026-10-05 21:51 ET: nine Devin jobs, one usable account, all nine
silent for 55 minutes). With Cursor capacity 1, a second `seat run --class` exits 4 at once with a capacity envelope
and never starts the vendor CLI, not even `cursor-agent models`; the first run's heartbeat keeps its lease past the
TTL; its release on exit frees the slot; a failed run releases `failed`; SIGTERM and Ctrl-C release `cancelled` and
stop the CLI; a refusal and agent-lb down launch nothing; a lost lease stops the CLI. No `--class` and a pinned
`--account` run unreserved, as today, with the reason in the dispatch row. `--class verify` carries the author vendor.

Runs a real agent-lb server (fresh data dir), the real clients/route as ROUTE_BIN and the real clients/seat; the only
stand-in is the vendor CLI (a fake `cursor-agent`, as in test_seat_cli.py). Pools come from route's fixture dir.

Fix round (review of 726658ca, 2026-10-06): the last tests swap in a scripted route to put a signal or a thread-start
failure at exact points of the reserve and release, which a real route cannot time."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator

import pytest

from tests.integration.test_route_reservations import route, server  # noqa: F401  (module-scoped server fixture)
from tests.unit.test_route_ladder import SCRIPT as ROUTE
from tests.unit.test_route_ladder import setup, table_copy

REPO = Path(__file__).resolve().parents[2]
SEAT = REPO / "clients" / "seat"
CONCURRENCY = {"per_account": {"cursor": 1, "devin": 1, "openai": 3, "anthropic": 4}, "default": 2,
               "ttl_s": 4, "heartbeat_s": 1}

FAKE_CURSOR = """#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
if sys.argv[1:] == ["models"]:
    with open(os.environ["FAKE_INVOCATIONS"], "a") as log:
        log.write("models\\n")
    print("Available models")
    raise SystemExit(0)
prompt = sys.stdin.read()
with open(os.environ["FAKE_INVOCATIONS"], "a") as log:
    log.write(prompt.strip() + "\\n")
(Path(os.environ["FAKE_GATE_DIR"]) / (prompt.strip() + ".pid")).write_text(str(os.getpid()))
gate = Path(os.environ["FAKE_GATE_DIR"]) / prompt.strip()
deadline = time.monotonic() + 120
while prompt.startswith("hold") and not gate.exists() and time.monotonic() < deadline:
    time.sleep(0.1)
if prompt.startswith("fail"):
    print(json.dumps({"type": "result", "is_error": True, "result": "tests failed"}))
    raise SystemExit(1)
print(json.dumps({"type": "result", "is_error": False, "result": "done", "session_id": "chat-1",
                  "usage": {"inputTokens": 10, "outputTokens": 2, "cacheReadTokens": 0}}))
"""

pytestmark = pytest.mark.timeout(600)


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
    added = subprocess.run([sys.executable, str(SEAT), "add", "cursor-a", "--vendor", "cursor", "--api-key-file",
                            str(key)], capture_output=True, text=True, timeout=60, env=base, check=False)
    assert added.returncode == 0, added.stderr
    yield base
    # A holder a failed assertion left running would keep the shared server's slot and fail every later test.
    while HOLDERS:
        holder = HOLDERS.pop()
        if holder.poll() is None:
            holder.terminate()
            try:
                holder.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                holder.kill()
                holder.communicate()


def seat_argv(env: dict[str, str], prompt: str, *extra: str) -> list[str]:
    return [sys.executable, str(SEAT), "run", "--vendor", "cursor", "--model", "grok-latest", *extra,
            "--cwd", str(Path(env["FAKE_GATE_DIR"]).parent), "--", prompt]


def run_seat(env: dict[str, str], prompt: str, *extra: str) -> tuple[int, dict]:
    result = subprocess.run(seat_argv(env, prompt, *extra), capture_output=True, text=True, timeout=120, env=env,
                            check=False)
    try:
        return result.returncode, json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.fail(f"seat run printed no JSON (rc {result.returncode}):\n{result.stdout}\n{result.stderr}")


def start_seat(env: dict[str, str], prompt: str) -> subprocess.Popen:
    process = subprocess.Popen(seat_argv(env, prompt, "--class", "mechanical"), stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, env=env)
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


def test_a_second_seat_run_waits_without_launching_and_release_frees_the_slot(env: dict[str, str]) -> None:
    holder = start_seat(env, "hold1")
    [lease] = mine(reservations(env)["live"])
    assert (lease["seat"], lease["capacity_key"], lease["host"]) == ("cursor-seat", "cursor", "host-a"), lease
    assert lease["job"].startswith("seat/host-a/seat-"), lease

    # Capacity 1 is taken: the second run exits 4 with a capacity envelope and never starts the CLI, not even to
    # list models.
    models = invocations(env).count("models")
    rc, waited = run_seat(env, "second", "--class", "mechanical")
    assert rc == 4, waited
    assert waited["ok"] is False and waited["error"].startswith("capacity: "), waited
    assert isinstance(waited["wait"], int) and 5 <= waited["wait"] <= 60, waited
    assert waited["attempts"] == [] and waited["account"] is None, waited
    assert "second" not in invocations(env) and invocations(env).count("models") == models

    # The holder outlives its 4 s TTL on heartbeats; a third caller still waits.
    time.sleep(6)
    assert [row["reservation_id"] for row in mine(reservations(env)["live"])] == [lease["reservation_id"]]
    rc, still = run_seat(env, "third", "--class", "mechanical")
    assert rc == 4 and "third" not in invocations(env) and invocations(env).count("models") == models, still

    # No --class, or a pinned account, runs unreserved as today and says why in the dispatch row.
    rc, plain = run_seat(env, "plain")
    assert rc == 0 and plain["ok"], plain
    rc, pinned = run_seat(env, "pinned", "--class", "mechanical", "--account", "cursor-a")
    assert rc == 0 and pinned["ok"], pinned
    dispatch = {row["session_id"]: row for row in ledger(env, "dispatch")}
    assert dispatch[plain["run_id"]]["reservation"] is None and dispatch[plain["run_id"]]["why"] == "no --class"
    assert dispatch[pinned["run_id"]]["reservation"] is None and "--account" in dispatch[pinned["run_id"]]["why"]
    assert len(mine(reservations(env)["live"])) == 1

    # The holder finishes ok: its lease is released `ok` and the slot is free for the next run.
    open_gate(env, "hold1")
    out, err = holder.communicate(timeout=90)
    assert holder.returncode == 0, out + err
    held = json.loads(out)
    dispatch = {row["session_id"]: row for row in ledger(env, "dispatch")}
    assert dispatch[held["run_id"]]["reservation"] == lease["reservation_id"]
    listing = reservations(env)
    assert mine(listing["live"]) == []
    [ended] = [row for row in listing["recent"] if row["reservation_id"] == lease["reservation_id"]]
    assert ended["outcome"] == "ok", ended
    rc, after = run_seat(env, "after", "--class", "mechanical")
    assert rc == 0 and after["ok"] and "after" in invocations(env), after


def test_failure_and_sigterm_release_and_agent_lb_down_launches_nothing(env: dict[str, str]) -> None:
    rc, failed = run_seat(env, "fail1", "--class", "mechanical")
    assert rc == 1 and failed["ok"] is False, failed
    outcomes = {row["job"]: row["outcome"] for row in mine(reservations(env)["recent"])}
    assert f"seat/host-a/{failed['run_id']}" in outcomes and outcomes[f"seat/host-a/{failed['run_id']}"] == "failed"

    holder = start_seat(env, "hold2")
    [lease] = mine(reservations(env)["live"])
    holder.send_signal(signal.SIGTERM)
    holder.communicate(timeout=60)
    assert holder.returncode != 0
    listing = reservations(env)
    assert mine(listing["live"]) == []
    [ended] = [row for row in listing["recent"] if row["reservation_id"] == lease["reservation_id"]]
    assert ended["outcome"] == "cancelled", ended

    down = {**env, "AGENT_LB_URL": "http://127.0.0.1:1"}
    before = invocations(env)
    rc, error = run_seat(down, "down1", "--class", "mechanical")
    assert rc == 1 and error["ok"] is False and error["error"].startswith("capacity: "), error
    assert invocations(env) == before


def vendor_alive(env: dict[str, str], prompt: str) -> bool:
    pid = int((Path(env["FAKE_GATE_DIR"]) / f"{prompt}.pid").read_text())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return Path(f"/proc/{pid}/stat").exists() and Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"


def cursor_pool(env: dict[str, str], **fields: object) -> None:
    path = Path(env["ROUTE_FIXTURE_DIR"]) / "api_pools.json"
    document = json.loads(path.read_text())
    for pool in document["pools"]:
        if pool["id"] == "cursor-models":
            pool.update(fields)
    path.write_text(json.dumps(document))


def test_ctrl_c_cancels_and_a_refusal_launches_nothing(env: dict[str, str]) -> None:
    holder = start_seat(env, "hold3")
    [lease] = mine(reservations(env)["live"])
    holder.send_signal(signal.SIGINT)
    out, err = holder.communicate(timeout=60)
    assert holder.returncode == 128 + signal.SIGINT, out + err
    assert json.loads(out)["error"] == "cancelled by signal SIGINT", out
    assert not vendor_alive(env, "hold3")
    [ended] = [row for row in reservations(env)["recent"] if row["reservation_id"] == lease["reservation_id"]]
    assert ended["outcome"] == "cancelled", ended

    cursor_pool(env, status="exhausted", eligibleAccounts=0)
    before = invocations(env)
    rc, refused = run_seat(env, "refused1", "--class", "mechanical")
    assert rc == 2 and refused["ok"] is False and refused["error"].startswith("capacity: "), refused
    assert invocations(env) == before and mine(reservations(env)["live"]) == []


def test_a_lost_lease_stops_the_vendor_cli(env: dict[str, str]) -> None:
    holder = start_seat(env, "hold4")
    [lease] = mine(reservations(env)["live"])
    # The lease ends behind the holder's back, as after an outage past its TTL: the slot is free for another run, so
    # the holder must stop its CLI on the next heartbeat, not run on unreserved.
    rc, _ = route(env, "release", lease["reservation_id"], "--outcome", "failed")
    assert rc == 0
    out, err = holder.communicate(timeout=30)
    assert holder.returncode == 1, out + err
    envelope = json.loads(out)
    assert envelope["ok"] is False and "was lost" in envelope["error"], envelope
    assert envelope["attempts"][-1]["outcome"] == "reservation_lost", envelope
    assert not vendor_alive(env, "hold4")


def test_verify_carries_the_author_vendor(env: dict[str, str]) -> None:
    rc, reviewed = run_seat(env, "verify1", "--class", "verify", "--author-vendor", "openai")
    assert rc == 0 and reviewed["ok"], reviewed
    [ended] = [row for row in reservations(env)["recent"] if row["job"] == f"seat/host-a/{reviewed['run_id']}"]
    assert (ended["seat"], ended["outcome"]) == ("cursor-seat", "ok"), ended

    missing = subprocess.run(seat_argv(env, "verify2", "--class", "verify"), capture_output=True, text=True,
                             timeout=60, env=env, check=False)
    assert missing.returncode == 2 and "--author-vendor" in missing.stderr, missing.stderr
    assert "verify2" not in invocations(env)


# A scripted route: it logs each call and, where told, signals seat at an exact point of the reserve or release.
SCRIPTED_ROUTE = """#!/bin/sh
echo "$*" >> "$ROUTE_CALLS"
case "$1" in
  reserve)
    [ -n "$SIGNAL_ON_RESERVE" ] && kill -"$SIGNAL_ON_RESERVE" "$PPID"
    echo '{"status": "reserved", "reservation_id": "rsv-000000000009", "heartbeat_s": 300}' ;;
  release)
    [ -n "$SIGNAL_ON_RELEASE" ] && kill -"$SIGNAL_ON_RELEASE" "$PPID" && sleep 1
    echo "released $4" >> "$ROUTE_CALLS"
    echo '{"status": "released"}' ;;
  heartbeat) echo '{"status": "live"}' ;;
  *) echo grok-9-medium-fast ;;
esac
"""

# Runs seat with Thread.start failing, as when the host is out of threads.
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
    (seats / "accounts.json").write_text(json.dumps({"accounts": [{"id": "fixture", "vendor": "cursor",
                                                                    "auth": "login"}]}))
    (tmp_path / "gates").mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ROUTE_", "SEAT_", "AGENT_LB_", "CURSOR_"))}
    env.update(HOME=str(tmp_path), SEAT_HOME=str(seats), ROUTE_LEDGER=str(tmp_path / "ledger"), ROUTE_HOST="host-a",
               ROUTE_BIN=str(_script(tmp_path / "route", SCRIPTED_ROUTE)), ROUTE_CALLS=str(tmp_path / "calls"),
               SEAT_CURSOR_BIN=str(_script(tmp_path / "cursor-agent", FAKE_CURSOR)),
               FAKE_INVOCATIONS=str(tmp_path / "invocations.log"), FAKE_GATE_DIR=str(tmp_path / "gates"))
    return env


def route_calls(env: dict[str, str]) -> list[str]:
    path = Path(env["ROUTE_CALLS"])
    return path.read_text().splitlines() if path.exists() else []


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_a_signal_during_release_never_cuts_it_short(scripted: dict[str, str], signum: int) -> None:
    env = {**scripted, "SIGNAL_ON_RELEASE": str(int(signum))}
    rc, done = run_seat(env, "quick1", "--class", "mechanical")
    assert done["ok"] and "quick1" in invocations(env), done
    assert "released ok" in route_calls(env), route_calls(env)
    assert rc == 128 + signum


def test_a_signal_during_the_reserve_releases_and_launches_nothing(scripted: dict[str, str]) -> None:
    env = {**scripted, "SIGNAL_ON_RESERVE": "TERM"}
    result = subprocess.run(seat_argv(env, "early1", "--class", "mechanical"), capture_output=True, text=True,
                            timeout=60, env=env, check=False)
    assert result.returncode == 128 + signal.SIGTERM, result.stdout + result.stderr
    assert "released cancelled" in route_calls(env), route_calls(env)
    assert invocations(env) == []


def test_a_thread_start_failure_still_releases(scripted: dict[str, str]) -> None:
    launcher = Path(scripted["FAKE_GATE_DIR"]).parent / "no_threads.py"
    launcher.write_text(NO_THREADS)
    argv = [sys.executable, str(launcher), *seat_argv(scripted, "nothread1", "--class", "mechanical")[1:]]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=60, env=scripted, check=False)
    assert result.returncode != 0 and "can't start new thread" in result.stderr, result.stderr
    assert "released failed" in route_calls(scripted), route_calls(scripted)
    assert invocations(scripted) == []
