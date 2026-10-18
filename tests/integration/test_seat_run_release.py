from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.integration.seat_run_support import (
    NO_THREADS,
    STOP_BEFORE_FIRST_ATTEMPT,
    invocations,
    route_calls,
    run_seat,
    seat_argv,
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


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_a_signal_during_release_never_cuts_it_short(scripted: dict[str, str], signum: int) -> None:
    env = {**scripted, "SIGNAL_ON_RELEASE": str(int(signum))}
    rc, done = run_seat(env, "quick1", "--class", "mechanical")
    assert done["ok"] and "quick1" in invocations(env), done
    assert "released ok" in route_calls(env), route_calls(env)
    assert rc == 128 + signum


def test_a_signal_during_the_reserve_releases_and_launches_nothing(scripted: dict[str, str]) -> None:
    env = {**scripted, "SIGNAL_ON_RESERVE": "TERM"}
    result = subprocess.run(
        seat_argv(env, "early1", "--class", "mechanical"),
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        check=False,
    )
    assert result.returncode == 128 + signal.SIGTERM, result.stdout + result.stderr
    assert "released cancelled" in route_calls(env), route_calls(env)
    assert invocations(env) == []


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
    accounts.write_text(
        json.dumps(
            {
                "accounts": [
                    {"id": "fixture", "vendor": "cursor", "auth": "login"},
                    {"id": "fixture-2", "vendor": "cursor", "auth": "login"},
                ]
            }
        )
    )
    env = {**scripted, "FAKE_MODELS_SLEEP": "15"}
    started = time.monotonic()
    process = subprocess.Popen(
        seat_argv(env, "listing1", "--class", "mechanical"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        deadline = time.monotonic() + 30
        while "models" not in invocations(env) and time.monotonic() < deadline:
            time.sleep(0.1)
        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=90)
    finally:
        if process.poll() is None:
            process.kill()

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
