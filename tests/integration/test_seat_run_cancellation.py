from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path

import pytest

from tests.integration.seat_run_support import (
    cursor_pool,
    invocations,
    mine,
    pid_alive,
    reservations,
    run_seat,
    start_seat,
    vendor_alive,
)
from tests.integration.seat_run_support import (
    env as env,
)
from tests.integration.test_route_reservations import route  # noqa: F401  (module-scoped server fixture)
from tests.integration.test_route_reservations import server as server

pytestmark = pytest.mark.timeout(600)


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
