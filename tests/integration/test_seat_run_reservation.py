from __future__ import annotations

import json
import signal
import time
from pathlib import Path

import pytest

from tests.integration.route_support import SCRIPT as ROUTE
from tests.integration.seat_run_support import (
    env as env,
)
from tests.integration.seat_run_support import (
    invocations,
    ledger,
    mine,
    open_gate,
    reservations,
    run_seat,
    start_seat,
)
from tests.integration.test_route_reservations import route  # noqa: F401  (module-scoped server fixture)
from tests.integration.test_route_reservations import server as server

pytestmark = pytest.mark.timeout(600)


def test_a_second_seat_run_waits_without_launching_and_release_frees_the_slot(env: dict[str, str]) -> None:
    holder = start_seat(env, "hold1")
    [lease] = mine(reservations(env)["live"])
    assert (lease["seat"], lease["capacity_key"], lease["host"]) == ("cursor-seat", "cursor", "host-a"), lease
    assert lease["job"].startswith("seat/host-a/seat-"), lease

    models = invocations(env).count("models")
    rc, waited = run_seat(env, "second", "--class", "mechanical")
    assert rc == 4, waited
    assert waited["ok"] is False and waited["error"].startswith("capacity: "), waited
    assert isinstance(waited["wait"], int) and 5 <= waited["wait"] <= 60, waited
    assert waited["attempts"] == [] and waited["account"] is None, waited
    assert "second" not in invocations(env) and invocations(env).count("models") == models

    time.sleep(6)
    assert [row["reservation_id"] for row in mine(reservations(env)["live"])] == [lease["reservation_id"]]
    rc, still = run_seat(env, "third", "--class", "mechanical")
    assert rc == 4 and "third" not in invocations(env) and invocations(env).count("models") == models, still

    rc, plain = run_seat(env, "plain")
    assert rc == 0 and plain["ok"], plain
    rc, pinned = run_seat(env, "pinned", "--class", "mechanical", "--account", "cursor-a")
    assert rc == 0 and pinned["ok"], pinned
    dispatch = {row["session_id"]: row for row in ledger(env, "dispatch")}
    assert dispatch[plain["run_id"]]["reservation"] is None and dispatch[plain["run_id"]]["why"] == "no --class"
    assert dispatch[pinned["run_id"]]["reservation"] is None and "--account" in dispatch[pinned["run_id"]]["why"]
    assert len(mine(reservations(env)["live"])) == 1

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


def test_a_python_route_runs_under_the_interpreter_not_by_exec(env: dict[str, str], tmp_path: Path) -> None:

    route = tmp_path / "route-noexec"
    route.write_text(
        f"#!/usr/bin/env python3\nimport runpy, sys\nsys.argv[0] = {str(ROUTE)!r}\n"
        f"runpy.run_path({str(ROUTE)!r}, run_name='__main__')\n"
    )
    route.chmod(0o644)
    rc, ran = run_seat({**env, "ROUTE_BIN": str(route)}, "noexec", "--class", "mechanical")
    assert rc == 0 and ran["ok"] and "noexec" in invocations(env), ran
    dispatch = {row["session_id"]: row for row in ledger(env, "dispatch")}
    assert dispatch[ran["run_id"]]["reservation"], dispatch[ran["run_id"]]
    assert mine(reservations(env)["live"]) == []


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
