"""A4a scenario (Opus-written 2026-10-06; the implementer may not edit this file). Provider capacity is a lease held
in agent-lb, not a count each caller makes alone (complaint 2026-10-05 21:51 ET: nine jobs on Devin with one usable
account, all nine silent for 55 minutes). Nine concurrent `route reserve` callers from two hosts get exactly what the
fixture pools allow and the rest wait; a holder that never releases loses its lease at expiry; a heartbeat keeps it;
a failed release frees at once; a pinned request waits or is refused, never moved to another seat; agent-lb down
means nothing is reserved; `route pick` stays advisory and unchanged.

Runs a real agent-lb server (fresh data dir) and the real clients/route; pools come from the route fixture dir,
reservations never do."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.unit.test_route_ladder import SCRIPT, setup, table_copy

REPO = Path(__file__).resolve().parents[2]
BOOT_SECONDS = 240
CONCURRENCY = {"per_account": {"devin": 1, "openai": 3, "anthropic": 4, "cursor": 2}, "default": 2,
               "ttl_s": 1200, "heartbeat_s": 300}

pytestmark = pytest.mark.timeout(600)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory):
    data = tmp_path_factory.mktemp("agent-lb-server")
    port = free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_LB_")}
    env.update(
        AGENT_LB_DATA_DIR=str(data),
        AGENT_LB_DATABASE_URL=f"sqlite+aiosqlite:///{data / 'store.db'}",
        AGENT_LB_RESERVATIONS_FILE=str(data / "reservations.json"),
        AGENT_LB_STAND_IN_FILE=str(data / "stand-ins.json"),
        AGENT_LB_SEAT_STATE=str(data / "seat-state.json"),
        AGENT_LB_UPSTREAM_BASE_URL="https://example.invalid/backend-api",
        AGENT_LB_USAGE_REFRESH_ENABLED="false",
        AGENT_LB_MODEL_REGISTRY_ENABLED="false",
        AGENT_LB_STICKY_SESSION_CLEANUP_ENABLED="false",
        AGENT_LB_QUOTA_PLANNER_SCHEDULER_ENABLED="false",
        AGENT_LB_ACCOUNTS_CACHE_WARMER_ENABLED="false",
    )
    log = (data / "server.log").open("w")
    proc = subprocess.Popen([sys.executable, "-c", "from app.cli import main; main()", "--host", "127.0.0.1",
                             "--port", str(port)], cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + BOOT_SECONDS
    while True:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=2) as response:
                if response.status == 200:
                    break
        except OSError:
            pass
        if proc.poll() is not None or time.monotonic() > deadline:
            proc.kill()
            pytest.fail("agent-lb did not boot:\n" + (data / "server.log").read_text()[-4000:])
        time.sleep(0.5)
    yield url
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()


def scenario_env(tmp_path: Path, url: str, name: str, **pool_updates: dict) -> dict[str, str]:
    root = tmp_path / name
    root.mkdir()
    env = setup(root, codex_low=False)
    pools_path = Path(env["ROUTE_FIXTURE_DIR"]) / "api_pools.json"
    document = json.loads(pools_path.read_text())
    updates = {"openai-codex": {"eligibleAccounts": 2}, "devin": {"eligibleAccounts": 1}, **pool_updates}
    for pool in document["pools"]:
        pool.update(updates.get(pool["id"], {}))
    pools_path.write_text(json.dumps(document))
    table = table_copy(root, "capacity", lambda t: t["policy"].update(concurrency=CONCURRENCY))
    env.update(AGENT_LB_URL=url, ROUTE_TABLE=str(table))
    return env


def route(env: dict[str, str], *args: str, host: str = "host-a") -> tuple[int, dict]:
    result = subprocess.run([sys.executable, str(SCRIPT), *args, "--json"], capture_output=True, text=True,
                            timeout=60, env={**env, "ROUTE_HOST": host}, check=False)
    try:
        body = json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.fail(f"route {' '.join(args)} printed no JSON (rc {result.returncode}):\n"
                    f"{result.stdout}\n{result.stderr}")
    return result.returncode, body


def without_clock(pick: dict) -> dict:
    return {**pick, "pace": {pool: {k: v for k, v in facts.items() if k != "hours_left"}
                             for pool, facts in (pick.get("pace") or {}).items()}}


def live(env: dict[str, str]) -> list[dict]:
    rc, body = route(env, "reservations")
    assert rc == 0, body
    return body["live"]


def test_capacity_is_a_lease_every_host_shares(tmp_path: Path, server: str) -> None:
    env = scenario_env(tmp_path, server, "main")
    _, advisory_before = route(env, "pick", "implement")

    # Nine at once from two hosts: Sol has 2 accounts x 3, Devin 1 x 1. Seven reserve, two wait; none is lost.
    def reserve(index: int) -> tuple[int, dict]:
        return route(env, "reserve", "implement", "--job", f"j{index}", host="host-a" if index % 2 else "host-b")
    with ThreadPoolExecutor(max_workers=9) as pool:
        results = list(pool.map(reserve, range(9)))
    reserved = [body for rc, body in results if rc == 0 and body["status"] == "reserved"]
    waiting = [body for rc, body in results if rc == 75 and body["status"] == "wait"]
    assert len(reserved) + len(waiting) == 9, results
    assert sorted(body["seat"] for body in reserved) == ["devin-seat"] + ["gpt-implementer"] * 6, results
    assert len(waiting) == 2
    for body in waiting:
        assert isinstance(body["wait"], int) and 5 <= body["wait"] <= 60, body
        assert "seat" not in body and "reservation_id" not in body, body
    [devin] = [body for body in reserved if body["seat"] == "devin-seat"]
    assert devin["rung"] == "swe2-high" and devin["overflow_from"] == ["sol-medium"], devin
    assert devin["capacity_key"] == "devin"
    for body in reserved:
        assert body["reservation_id"] and body["expires_at"] and body["model"], body
    rows = live(env)
    assert len(rows) == 7 and {row["host"] for row in rows} == {"host-a", "host-b"}, rows

    # The same job asking again gets its own lease back, not a second one.
    first_sol = next(body for body in reserved if body["seat"] == "gpt-implementer")
    rc, again = route(env, "reserve", "implement", "--job", first_sol["job"])
    assert rc == 0 and again["reservation_id"] == first_sol["reservation_id"], again
    assert len(live(env)) == 7

    # A pin on a full rung waits; it is never moved to another seat.
    rc, pinned = route(env, "reserve", "implement", "--job", "pin1", "--prefer", "devin-seat", "--reason", "A4a pin")
    assert rc == 75 and pinned["status"] == "wait" and "seat" not in pinned, pinned
    assert len(live(env)) == 7

    # A failed run's release frees its capacity at once; a second release changes nothing.
    rc, released = route(env, "release", devin["reservation_id"], "--outcome", "failed")
    assert rc == 0 and (released["status"], released["outcome"]) == ("released", "failed"), released
    rc, pinned = route(env, "reserve", "implement", "--job", "pin1", "--prefer", "devin-seat", "--reason", "A4a pin")
    assert rc == 0 and (pinned["status"], pinned["seat"]) == ("reserved", "devin-seat"), pinned
    rc, twice = route(env, "release", devin["reservation_id"], "--outcome", "ok")
    assert rc == 0 and twice["outcome"] == "failed", twice

    # A holder that dies without releasing holds its capacity only until expiry.
    rc, freed = route(env, "release", first_sol["reservation_id"], "--outcome", "ok")
    assert rc == 0 and freed["outcome"] == "ok"
    rc, dead = route(env, "reserve", "implement", "--job", "dead1", "--ttl", "5")
    assert rc == 0 and dead["seat"] == "gpt-implementer", dead
    rc, blocked = route(env, "reserve", "implement", "--job", "next1")
    assert rc == 75 and blocked["status"] == "wait", blocked
    time.sleep(6)
    rc, after = route(env, "reserve", "implement", "--job", "next1")
    assert rc == 0 and after["seat"] == "gpt-implementer", after
    rc, listing = route(env, "reservations")
    expired = [row for row in listing["recent"] if row["job"] == "dead1"]
    assert expired and expired[0]["outcome"] == "expired", listing

    # A heartbeat keeps a lease past its first expiry; a released lease cannot be renewed.
    route(env, "release", after["reservation_id"], "--outcome", "cancelled")
    rc, beating = route(env, "reserve", "implement", "--job", "hb1", "--ttl", "4")
    assert rc == 0, beating
    for _ in range(2):
        time.sleep(2)
        rc, beat = route(env, "heartbeat", beating["reservation_id"], "--ttl", "4")
        assert rc == 0 and beat["status"] == "reserved" and beat["expires_at"] > beating["expires_at"], beat
    rc, still = route(env, "reserve", "implement", "--job", "probe1")
    assert rc == 75 and still["status"] == "wait", still
    route(env, "release", beating["reservation_id"], "--outcome", "ok")
    rc, gone = route(env, "heartbeat", beating["reservation_id"])
    assert rc == 2 and gone["status"] == "gone", gone

    # `route pick` is advisory: reservations do not change what it prints. Pace `hours_left` is counted from the
    # clock, so it is the one field allowed to move between the two calls.
    _, advisory_after = route(env, "pick", "implement")
    assert without_clock(advisory_after) == without_clock(advisory_before)


def test_a_pin_that_cannot_run_is_refused_and_agent_lb_down_reserves_nothing(tmp_path: Path, server: str) -> None:
    empty = scenario_env(tmp_path, server, "empty", devin={"status": "exhausted", "eligibleAccounts": 0})
    before = len(live(empty))
    rc, refused = route(empty, "reserve", "implement", "--job", "pin2", "--prefer", "devin-seat", "--reason", "A4a")
    assert rc == 2 and refused["status"] == "refused" and "pool devin exhausted" in refused["reason"], refused
    assert "seat" not in refused and len(live(empty)) == before

    down = scenario_env(tmp_path, server, "down")
    down["AGENT_LB_URL"] = "http://127.0.0.1:1"
    rc, error = route(down, "reserve", "implement", "--job", "down1")
    assert rc == 1 and (error["status"], error["error"]) == ("error", "reservations_unavailable"), error
    assert "seat" not in error
