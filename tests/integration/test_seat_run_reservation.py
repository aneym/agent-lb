"""A4d scenario (Opus-written 2026-10-06; the implementer may not edit this file). The devin-seat and cursor-seat
forwarders call `seat run --class <class>` directly, never factory seat-run, so A4a's reservations do nothing for them
unless `seat run` takes one itself (complaint 2026-10-05 21:51 ET: nine Devin jobs, one usable account, all nine
silent for 55 minutes). With Cursor capacity 1, a second `seat run --class` exits 4 at once with a capacity envelope
and never starts the vendor CLI; the first run's heartbeat keeps its lease past the TTL; its release on exit frees
the slot; a failed run releases `failed`; SIGTERM releases `cancelled`; agent-lb down launches nothing. No `--class`
and a pinned `--account` run unreserved, as today, with the reason in the dispatch row.

Runs a real agent-lb server (fresh data dir), the real clients/route as ROUTE_BIN and the real clients/seat; the only
stand-in is the vendor CLI (a fake `cursor-agent`, as in test_seat_cli.py). Pools come from route's fixture dir."""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
from pathlib import Path

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
    print("Available models")
    raise SystemExit(0)
prompt = sys.stdin.read()
with open(os.environ["FAKE_INVOCATIONS"], "a") as log:
    log.write(prompt.strip() + "\\n")
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


@pytest.fixture
def env(tmp_path: Path, server: str) -> dict[str, str]:  # noqa: F811
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
    return base


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

    # Capacity 1 is taken: the second run exits 4 with a capacity envelope and never starts the CLI.
    rc, waited = run_seat(env, "second", "--class", "mechanical")
    assert rc == 4, waited
    assert waited["ok"] is False and waited["error"].startswith("capacity: "), waited
    assert isinstance(waited["wait"], int) and 5 <= waited["wait"] <= 60, waited
    assert waited["attempts"] == [] and waited["account"] is None, waited
    assert "second" not in invocations(env)

    # The holder outlives its 4 s TTL on heartbeats; a third caller still waits.
    time.sleep(6)
    assert [row["reservation_id"] for row in mine(reservations(env)["live"])] == [lease["reservation_id"]]
    rc, still = run_seat(env, "third", "--class", "mechanical")
    assert rc == 4 and "third" not in invocations(env), still

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
