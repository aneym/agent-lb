from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.integration.seat_run_support import (
    HOLDERS,
    UNKILLABLE,
    FailingReleaseLB,
    invocations,
    route_calls,
    seat_argv,
    slow_discovery,
)
from tests.integration.seat_run_support import (
    env as env,
)
from tests.integration.seat_run_support import (
    scripted as scripted,
)
from tests.integration.test_route_reservations import route  # noqa: F401  (module-scoped server fixture)
from tests.integration.test_route_reservations import server as server

pytestmark = pytest.mark.timeout(600)


def test_a_stop_never_releases_while_a_process_survives_sigkill(scripted: dict[str, str], tmp_path: Path) -> None:
    launcher = tmp_path / "unkillable.py"
    launcher.write_text(UNKILLABLE)
    survivor = tmp_path / "survivor"
    env = {**scripted, "SURVIVOR": str(survivor)}
    process = subprocess.Popen(
        [sys.executable, str(launcher), *seat_argv(env, "hold6", "--class", "mechanical")[1:]],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    HOLDERS.append(process)
    deadline = time.monotonic() + 30
    while "hold6" not in invocations(env) and time.monotonic() < deadline:
        time.sleep(0.1)
    survivor.write_text("D")
    process.send_signal(signal.SIGTERM)
    time.sleep(3)
    assert process.poll() is None and not any(line.startswith("released") for line in route_calls(env)), (
        "the run released its capacity while a process of its group was alive"
    )
    survivor.unlink()
    out, err = process.communicate(timeout=30)
    assert process.returncode == 128 + signal.SIGTERM, out + err
    assert "released cancelled" in route_calls(env), route_calls(env)


def test_a_provisional_hold_that_cannot_be_released_is_never_reported_as_another_rung(
    env: dict[str, str], tmp_path: Path
) -> None:

    lb = FailingReleaseLB()
    try:
        env = {**slow_discovery(env, tmp_path, 0), "AGENT_LB_URL": lb.url}
        rc, body = route(
            env,
            "reserve",
            "mechanical",
            "--job",
            "grok-concrete",
            "--prefer",
            "cursor-seat",
            "--model",
            "grok-4.7-low",
            "--reason",
            "concrete grok",
        )
        assert rc != 0 and body["status"] != "reserved", body
        assert "composer" in body["reason"] and lb.releases >= 1, body
    finally:
        lb.server.shutdown()
