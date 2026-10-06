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
failure at exact points of the reserve and release, which a real route cannot time.

Fix round 2 (review of 21d53dbf, 2026-10-06, Opus): with a cold model cache no vendor model listing runs before the hold
(route lists only once it holds capacity); a stop waits for SIGTERM-resistant descendants; a stop before the first
attempt, or during a model listing, ends with the cancellation envelope and the signal's exit code; the run uses the
reserved model or refuses (a verify for xAI work never runs Grok); liveness is checked portably."""

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
    if os.environ.get("FAKE_MODELS_SLEEP"):
        # A slow listing that, when it finishes, does not offer the run's model.
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
    # A descendant that ignores SIGTERM and holds none of the leader's pipes, so the leader's exit closes them.
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


# The Cursor model on the mechanical ladder (rung grok-low); route reserves the rung that runs it.
MODEL = "grok-latest-low"


def seat_argv(env: dict[str, str], prompt: str, *extra: str, model: str = MODEL) -> list[str]:
    return [sys.executable, str(SEAT), "run", "--vendor", "cursor", "--model", model, *extra,
            "--cwd", str(Path(env["FAKE_GATE_DIR"]).parent), "--", prompt]


def run_seat(env: dict[str, str], prompt: str, *extra: str, model: str = MODEL) -> tuple[int, dict]:
    result = subprocess.run(seat_argv(env, prompt, *extra, model=model), capture_output=True, text=True, timeout=120,
                            env=env, check=False)
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


def vendor_alive(env: dict[str, str], prompt: str, suffix: str = "pid") -> bool:
    pid = int((Path(env["FAKE_GATE_DIR"]) / f"{prompt}.{suffix}").read_text())
    return pid_alive(pid)


def pid_alive(pid: int) -> bool:
    """os.kill(pid, 0) on every platform; a zombie (reported by ps on macOS and Linux alike) counts as dead."""
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


@pytest.mark.parametrize("stops", [1, 2])
def test_a_stop_waits_for_descendants_that_ignore_sigterm(env: dict[str, str], stops: int) -> None:
    prompt = f"orphan{stops}"
    holder = start_seat(env, prompt)
    child_file = Path(env["FAKE_GATE_DIR"]) / f"{prompt}.child"
    deadline = time.monotonic() + 30
    while not child_file.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    child = int(child_file.read_text())
    [lease] = mine(reservations(env)["live"])
    try:
        # The leader dies on SIGTERM and its pipes close; the child ignores SIGTERM. The run must not end, and its
        # capacity must not be released, until SIGKILL has taken the child too: after the grace, or at once on a
        # second stop signal.
        started = time.monotonic()
        holder.send_signal(signal.SIGTERM)
        if stops == 2:
            time.sleep(1)
            holder.send_signal(signal.SIGTERM)
        out, err = holder.communicate(timeout=60)
        assert holder.returncode == 128 + signal.SIGTERM, out + err
        assert not pid_alive(child), "a SIGTERM-resistant descendant outlived the stopped run"
        if stops == 2:
            assert time.monotonic() - started < 8, "a second stop signal did not escalate to SIGKILL"
        [ended] = [row for row in reservations(env)["recent"] if row["reservation_id"] == lease["reservation_id"]]
        assert ended["outcome"] == "cancelled", ended
    finally:
        if pid_alive(child):
            os.kill(child, signal.SIGKILL)


def cold_cursor_cache(env: dict[str, str]) -> None:
    cache = Path(env["ROUTE_MODELS_CACHE"]).with_name("route-cursor-models.json")
    cache.unlink(missing_ok=True)
    cache.with_suffix(".failed.json").unlink(missing_ok=True)


def launched_model(env: dict[str, str], prompt: str) -> str:
    argv = json.loads((Path(env["FAKE_GATE_DIR"]) / f"{prompt}.argv").read_text())
    return argv[argv.index("--model") + 1]


def test_verify_carries_the_author_vendor_and_runs_only_the_reserved_model(env: dict[str, str]) -> None:
    sonnet = "claude-sonnet-5-5-high"
    rc, reviewed = run_seat(env, "verify1", "--class", "verify", "--author-vendor", "openai", model=sonnet)
    assert rc == 0 and reviewed["ok"], reviewed
    [ended] = [row for row in reservations(env)["recent"] if row["job"] == f"seat/host-a/{reviewed['run_id']}"]
    assert (ended["seat"], ended["outcome"]) == ("cursor-seat", "ok"), ended
    assert launched_model(env, "verify1") == ended["model"] == reviewed["model"] == sonnet

    # Grok reviewing xAI's own work: Cursor's only verify rung runs Sonnet, so route reserves nothing for Grok and the
    # run is refused without starting the vendor CLI. With a cold cache route can tell only once it holds the rung, so
    # it releases the hold before it refuses.
    for cache in ("cold", "warm"):
        if cache == "cold":
            cold_cursor_cache(env)
        before = invocations(env)
        rc, crossed = run_seat(env, f"verify3{cache}", "--class", "verify", "--author-vendor", "xai",
                               model="grok-latest")
        assert rc == 2 and crossed["ok"] is False and crossed["error"].startswith("capacity: "), (cache, crossed)
        assert "grok-latest" in crossed["error"], (cache, crossed)
        assert invocations(env) == before and mine(reservations(env)["live"]) == [], cache

    missing = subprocess.run(seat_argv(env, "verify2", "--class", "verify"), capture_output=True, text=True,
                             timeout=60, env=env, check=False)
    assert missing.returncode == 2 and "--author-vendor" in missing.stderr, missing.stderr
    assert "verify2" not in invocations(env)


# Stands in for `cursor-agent --list-models` (route's discovery): logs how many of this host's seat reservations are
# live when it runs, then lists the fixture models.
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


def test_a_cold_model_cache_is_listed_only_under_the_hold(env: dict[str, str], tmp_path: Path) -> None:
    log = tmp_path / "discovery.log"
    env = {**env, "DISCOVERY_LOG": str(log),
           "ROUTE_CURSOR_MODELS_CMD": f"{sys.executable} {_script(tmp_path / 'discovery', DISCOVERY)}"}
    def listings() -> list[str]:
        return log.read_text().split() if log.exists() else []

    cold_cursor_cache(env)
    holder = start_seat(env, "hold5")
    # Route listed Cursor's models for the holder only once its reservation was live.
    assert listings() and all(line != "live=0" for line in listings()), listings()
    assert launched_model(env, "hold5") == "grok-4.7-low"

    # A run that waits for capacity lists nothing at all, cold cache or not.
    cold_cursor_cache(env)
    before = listings()
    rc, waited = run_seat(env, "second5", "--class", "mechanical")
    assert rc == 4 and waited["error"].startswith("capacity: "), waited
    assert listings() == before and "second5" not in invocations(env)

    open_gate(env, "hold5")
    out, err = holder.communicate(timeout=90)
    assert holder.returncode == 0, out + err


# A scripted route: it logs each call and, where told, signals seat at an exact point of the reserve or release.
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


# Runs seat with a SIGTERM to itself as it builds the first vendor command: after the account loop's stop check and
# before the attempt's own.
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


def test_a_stop_before_the_first_attempt_returns_the_cancellation_envelope(scripted: dict[str, str]) -> None:
    launcher = Path(scripted["FAKE_GATE_DIR"]).parent / "stop_first.py"
    launcher.write_text(STOP_BEFORE_FIRST_ATTEMPT)
    argv = [sys.executable, str(launcher), *seat_argv(scripted, "first1", "--class", "mechanical")[1:]]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=60, env=scripted, check=False)
    assert result.returncode == 128 + signal.SIGTERM, result.stdout + result.stderr
    assert json.loads(result.stdout)["error"] == "cancelled by signal SIGTERM", result.stdout
    assert "released cancelled" in route_calls(scripted), route_calls(scripted)
    assert "first1" not in invocations(scripted)


def test_a_thread_start_failure_still_releases(scripted: dict[str, str]) -> None:
    launcher = Path(scripted["FAKE_GATE_DIR"]).parent / "no_threads.py"
    launcher.write_text(NO_THREADS)
    argv = [sys.executable, str(launcher), *seat_argv(scripted, "nothread1", "--class", "mechanical")[1:]]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=60, env=scripted, check=False)
    assert result.returncode != 0 and "can't start new thread" in result.stderr, result.stderr
    assert "released failed" in route_calls(scripted), route_calls(scripted)
    assert invocations(scripted) == []


def test_a_model_other_than_the_reserved_one_is_refused(scripted: dict[str, str]) -> None:
    rc, refused = run_seat(scripted, "other1", "--class", "mechanical", model="composer-latest")
    assert rc == 2 and refused["ok"] is False, refused
    assert "grok-4.7-low" in refused["error"] and "composer-latest" in refused["error"], refused
    assert "released failed" in route_calls(scripted) and invocations(scripted) == []


def test_a_signal_during_a_model_listing_stops_it_and_lists_no_other_account(scripted: dict[str, str]) -> None:
    accounts = Path(scripted["SEAT_HOME"]) / "accounts.json"
    accounts.write_text(json.dumps({"accounts": [{"id": "fixture", "vendor": "cursor", "auth": "login"},
                                                 {"id": "fixture-2", "vendor": "cursor", "auth": "login"}]}))
    env = {**scripted, "FAKE_MODELS_SLEEP": "15"}
    started = time.monotonic()
    process = subprocess.Popen(seat_argv(env, "listing1", "--class", "mechanical"), stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, env=env)
    try:
        deadline = time.monotonic() + 30
        while "models" not in invocations(env) and time.monotonic() < deadline:
            time.sleep(0.1)
        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=90)
    finally:
        if process.poll() is None:
            process.kill()
    # The listing's group is stopped at once, no second account is listed, and a half-done listing never turns the
    # stop into a "not a Cursor model" refusal.
    assert process.returncode == 128 + signal.SIGTERM, out + err
    assert time.monotonic() - started < 15, "the model listing ran to the end after the stop"
    assert invocations(env) == ["models"], invocations(env)
    assert "released cancelled" in route_calls(env), route_calls(env)


def test_a_failed_first_release_is_retried(scripted: dict[str, str]) -> None:
    env = {**scripted, "FAIL_FIRST_RELEASE": "1"}
    rc, done = run_seat(env, "quick2", "--class", "mechanical")
    assert rc == 0 and done["ok"], done
    calls = route_calls(env)
    assert [line.split()[0] for line in calls].count("release") == 2 and "released ok" in calls, calls


# a4d-4 (review of 594c1d9d, 2026-10-06, Opus): route refused the forwarders' own default models, so every
# cursor-seat and devin-seat run with --class exited 2. A family alias names its rung at any effort (the ladder sets the
# effort); another family is still refused.
def test_a_forwarder_default_alias_runs_its_rung_at_the_ladder_effort(env: dict[str, str]) -> None:
    rc, ran = run_seat(env, "default1", "--class", "mechanical", model="grok-latest")
    assert rc == 0 and ran["ok"], ran
    assert launched_model(env, "default1") == ran["model"] == "grok-4.7-low"

    for task_class in ("mechanical", "explore"):
        rc, held = route(env, "reserve", task_class, "--job", f"swe-{task_class}", "--prefer", "devin-seat",
                         "--model", "swe-latest", "--reason", "devin-seat default")
        assert rc == 0 and (held["rung"], held["model"]) == ("swe2-medium", "swe-2-medium"), (task_class, held)
        assert route(env, "release", held["reservation_id"], "--outcome", "ok")[0] == 0

    # Cursor's only explore rung runs Composer: Grok there is another family, refused with what the rung runs.
    rc, refused = route(env, "reserve", "explore", "--job", "grok-explore", "--prefer", "cursor-seat",
                        "--model", "grok-latest-low", "--reason", "scouting")
    assert rc == 2 and refused["status"] == "refused" and "composer-latest" in refused["reason"], refused


# Stands in for a slow `cursor-agent --list-models`: records its pid, then lists after ROUTE_TEST_LIST_SLEEP seconds.
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
    return {**env, "DISCOVERY_PID": str(tmp_path / "discovery.pid"), "ROUTE_TEST_LIST_SLEEP": str(seconds),
            "ROUTE_CURSOR_MODELS_CMD": f"{sys.executable} {_script(tmp_path / 'slow-discovery', SLOW_DISCOVERY)}"}


def test_a_stop_during_the_model_listing_under_the_hold_ends_it_and_releases(env: dict[str, str],
                                                                            tmp_path: Path) -> None:
    env = slow_discovery(env, tmp_path, 8)
    process = subprocess.Popen(seat_argv(env, "listed1", "--class", "mechanical"), stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, env=env)
    HOLDERS.append(process)
    marker = tmp_path / "discovery.pid"
    deadline = time.monotonic() + 60
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    [lease] = mine(reservations(env)["live"])
    stopped = time.monotonic()
    process.send_signal(signal.SIGTERM)
    out, err = process.communicate(timeout=60)
    # Route's listing stops with the run, well before it would have finished, and the hold it ran under is released.
    assert time.monotonic() - stopped < 5, "the model listing under the hold ran on after the stop"
    assert process.returncode == 128 + signal.SIGTERM, out + err
    assert json.loads(out)["error"] == "cancelled by signal SIGTERM", out
    assert not pid_alive(int(marker.read_text()))
    assert mine(reservations(env)["live"]) == [] and "listed1" not in invocations(env)
    [ended] = [row for row in reservations(env)["recent"] if row["reservation_id"] == lease["reservation_id"]]
    assert ended["outcome"] == "cancelled", ended


def test_a_hold_that_expires_during_the_model_listing_runs_nothing(env: dict[str, str], tmp_path: Path) -> None:
    # The fixture TTL is 4 s and seat's heartbeats start only once route returns: a 6 s listing outlives the hold.
    env = slow_discovery(env, tmp_path, 6)
    rc, refused = run_seat(env, "expired1", "--class", "mechanical")
    assert rc == 2 and refused["ok"] is False and "lost while its models were listed" in refused["error"], refused
    assert "expired1" not in invocations(env) and mine(reservations(env)["live"]) == []


def test_the_reservation_stores_the_model_resolved_under_the_hold(env: dict[str, str]) -> None:
    cold_cursor_cache(env)
    holder = start_seat(env, "hold7")
    [lease] = mine(reservations(env)["live"])
    assert lease["model"] == launched_model(env, "hold7") == "grok-4.7-low", lease
    open_gate(env, "hold7")
    out, err = holder.communicate(timeout=90)
    assert holder.returncode == 0, out + err
    [ended] = [row for row in reservations(env)["recent"] if row["reservation_id"] == lease["reservation_id"]]
    assert (ended["model"], ended["outcome"]) == ("grok-4.7-low", "ok"), ended


def test_an_implement_rung_whose_only_reviewer_does_not_resolve_is_not_held(env: dict[str, str],
                                                                            tmp_path: Path) -> None:
    # Claude and Codex are spent, so Cursor's Sonnet is the only reviewer for Devin's work; Cursor's list (cold until
    # the hold) has no Sonnet. Devin's own list is warm, so only the reviewer waits on discovery.
    path = Path(env["ROUTE_FIXTURE_DIR"]) / "api_pools.json"
    document = json.loads(path.read_text())
    for pool in document["pools"]:
        if pool["id"] in ("anthropic-general", "openai-codex"):
            pool.update(status="exhausted", eligibleAccounts=0, aggregateRemainingPercent=0.0)
    path.write_text(json.dumps(document))
    env = slow_discovery(env, tmp_path, 0)
    rc, refused = route(env, "reserve", "implement", "--job", "devin-unreviewed", "--prefer", "devin-seat",
                        "--model", "swe-latest", "--reason", "devin implement")
    assert rc == 2 and refused["status"] == "refused" and "reviewer" in refused["reason"], refused
    assert [row for row in reservations(env)["live"] if row["job"] == "devin-unreviewed"] == []


def test_a_signal_during_the_alias_resolution_returns_the_cancellation_envelope(scripted: dict[str, str]) -> None:
    env = {**scripted, "SIGNAL_ON_RESOLVE": "TERM"}
    result = subprocess.run(seat_argv(env, "resolving1", "--class", "mechanical", model="composer-latest"),
                            capture_output=True, text=True, timeout=60, env=env, check=False)
    assert result.returncode == 128 + signal.SIGTERM, result.stdout + result.stderr
    assert json.loads(result.stdout)["error"] == "cancelled by signal SIGTERM", result.stdout + result.stderr
    assert "released cancelled" in route_calls(env) and invocations(env) == []


# Runs seat with a short grace and with its process group reported alive while SURVIVOR exists, as when a descendant
# is blocked in uninterruptible I/O and outlives SIGKILL.
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


def test_a_stop_never_releases_while_a_process_survives_sigkill(scripted: dict[str, str], tmp_path: Path) -> None:
    launcher = tmp_path / "unkillable.py"
    launcher.write_text(UNKILLABLE)
    survivor = tmp_path / "survivor"
    env = {**scripted, "SURVIVOR": str(survivor)}
    process = subprocess.Popen([sys.executable, str(launcher), *seat_argv(env, "hold6", "--class", "mechanical")[1:]],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    HOLDERS.append(process)
    deadline = time.monotonic() + 30
    while "hold6" not in invocations(env) and time.monotonic() < deadline:
        time.sleep(0.1)
    survivor.write_text("D")
    process.send_signal(signal.SIGTERM)
    time.sleep(3)
    assert process.poll() is None and not any(line.startswith("released") for line in route_calls(env)), \
        "the run released its capacity while a process of its group was alive"
    survivor.unlink()
    out, err = process.communicate(timeout=30)
    assert process.returncode == 128 + signal.SIGTERM, out + err
    assert "released cancelled" in route_calls(env), route_calls(env)


# A stand-in agent-lb whose release always fails, and which hands a job its live hold back as real agent-lb does.
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
                    row = holds.get(body["job"]) or {**body["candidates"][0], "reservation_id": "rsv-00000000000a",
                                                       "job": body["job"], "expires_at": "2099-01-01T00:00:00Z"}
                    holds[body["job"]] = row
                    self.reply(200, {**row, "status": "reserved"})

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def test_a_provisional_hold_that_cannot_be_released_is_never_reported_as_another_rung(env: dict[str, str],
                                                                                     tmp_path: Path) -> None:
    # A concrete Grok id with a cold cache: route provisionally holds Composer (the first Cursor rung), lists, finds
    # Composer runs composer-2.5, and must give that hold up. Its release fails; agent-lb would hand the next reserve
    # for the job the Composer hold back, so route must stop, not report Composer as the Grok reservation.
    lb = FailingReleaseLB()
    try:
        env = {**slow_discovery(env, tmp_path, 0), "AGENT_LB_URL": lb.url}
        rc, body = route(env, "reserve", "mechanical", "--job", "grok-concrete", "--prefer", "cursor-seat",
                         "--model", "grok-4.7-low", "--reason", "concrete grok")
        assert rc != 0 and body["status"] != "reserved", body
        assert "composer" in body["reason"] and lb.releases >= 1, body
    finally:
        lb.server.shutdown()
