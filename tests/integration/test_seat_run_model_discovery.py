from __future__ import annotations

import json
import signal
import subprocess
import time
from pathlib import Path

import pytest

from tests.integration.seat_run_support import (
    HOLDERS,
    cold_cursor_cache,
    invocations,
    launched_model,
    mine,
    open_gate,
    pid_alive,
    reservations,
    route_calls,
    run_seat,
    seat_argv,
    slow_discovery,
    start_seat,
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


def test_a_forwarder_default_alias_runs_its_rung_at_the_ladder_effort(env: dict[str, str]) -> None:
    rc, ran = run_seat(env, "default1", "--class", "mechanical", model="grok-latest")
    assert rc == 0 and ran["ok"], ran
    assert launched_model(env, "default1") == ran["model"] == "grok-4.7-low"

    for task_class in ("mechanical", "explore"):
        rc, held = route(
            env,
            "reserve",
            task_class,
            "--job",
            f"swe-{task_class}",
            "--prefer",
            "devin-seat",
            "--model",
            "swe-latest",
            "--reason",
            "devin-seat default",
        )
        assert rc == 0 and (held["rung"], held["model"]) == ("swe2-medium", "swe-2-medium"), (task_class, held)
        assert route(env, "release", held["reservation_id"], "--outcome", "ok")[0] == 0

    rc, refused = route(
        env,
        "reserve",
        "explore",
        "--job",
        "grok-explore",
        "--prefer",
        "cursor-seat",
        "--model",
        "grok-latest-low",
        "--reason",
        "scouting",
    )
    assert rc == 2 and refused["status"] == "refused" and "composer-latest" in refused["reason"], refused


def test_a_stop_during_the_model_listing_under_the_hold_ends_it_and_releases(
    env: dict[str, str], tmp_path: Path
) -> None:
    env = slow_discovery(env, tmp_path, 8)
    process = subprocess.Popen(
        seat_argv(env, "listed1", "--class", "mechanical"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    HOLDERS.append(process)
    marker = tmp_path / "discovery.pid"
    deadline = time.monotonic() + 60
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    [lease] = mine(reservations(env)["live"])
    stopped = time.monotonic()
    process.send_signal(signal.SIGTERM)
    out, err = process.communicate(timeout=60)

    assert time.monotonic() - stopped < 5, "the model listing under the hold ran on after the stop"
    assert process.returncode == 128 + signal.SIGTERM, out + err
    assert json.loads(out)["error"] == "cancelled by signal SIGTERM", out
    assert not pid_alive(int(marker.read_text()))
    assert mine(reservations(env)["live"]) == [] and "listed1" not in invocations(env)
    [ended] = [row for row in reservations(env)["recent"] if row["reservation_id"] == lease["reservation_id"]]
    assert ended["outcome"] == "cancelled", ended


def test_a_hold_that_expires_during_the_model_listing_runs_nothing(env: dict[str, str], tmp_path: Path) -> None:

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


def test_an_implement_rung_whose_only_reviewer_does_not_resolve_is_not_held(
    env: dict[str, str], tmp_path: Path
) -> None:

    path = Path(env["ROUTE_FIXTURE_DIR"]) / "api_pools.json"
    document = json.loads(path.read_text())
    for pool in document["pools"]:
        if pool["id"] in ("anthropic-general", "openai-codex"):
            pool.update(status="exhausted", eligibleAccounts=0, aggregateRemainingPercent=0.0)
    path.write_text(json.dumps(document))
    env = slow_discovery(env, tmp_path, 0)
    rc, refused = route(
        env,
        "reserve",
        "implement",
        "--job",
        "devin-unreviewed",
        "--prefer",
        "devin-seat",
        "--model",
        "swe-latest",
        "--reason",
        "devin implement",
    )
    assert rc == 2 and refused["status"] == "refused" and "reviewer" in refused["reason"], refused
    assert [row for row in reservations(env)["live"] if row["job"] == "devin-unreviewed"] == []


def test_a_signal_during_the_alias_resolution_returns_the_cancellation_envelope(scripted: dict[str, str]) -> None:
    env = {**scripted, "SIGNAL_ON_RESOLVE": "TERM"}
    result = subprocess.run(
        seat_argv(env, "resolving1", "--class", "mechanical", model="composer-latest"),
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        check=False,
    )
    assert result.returncode == 128 + signal.SIGTERM, result.stdout + result.stderr
    assert json.loads(result.stdout)["error"] == "cancelled by signal SIGTERM", result.stdout + result.stderr
    assert "released cancelled" in route_calls(env) and invocations(env) == []
