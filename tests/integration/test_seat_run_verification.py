from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration.seat_run_support import (
    DISCOVERY,
    _script,
    cold_cursor_cache,
    invocations,
    launched_model,
    mine,
    open_gate,
    reservations,
    run_seat,
    seat_argv,
    start_seat,
)
from tests.integration.seat_run_support import (
    env as env,
)
from tests.integration.test_route_reservations import route  # noqa: F401  (module-scoped server fixture)
from tests.integration.test_route_reservations import server as server

pytestmark = pytest.mark.timeout(600)


def test_verify_carries_the_author_vendor_and_runs_only_the_reserved_model(env: dict[str, str]) -> None:
    sonnet = "claude-sonnet-5-5-high"
    rc, reviewed = run_seat(env, "verify1", "--class", "verify", "--author-vendor", "openai", model=sonnet)
    assert rc == 0 and reviewed["ok"], reviewed
    [ended] = [row for row in reservations(env)["recent"] if row["job"] == f"seat/host-a/{reviewed['run_id']}"]
    assert (ended["seat"], ended["outcome"]) == ("cursor-seat", "ok"), ended
    assert launched_model(env, "verify1") == ended["model"] == reviewed["model"] == sonnet

    for cache in ("cold", "warm"):
        if cache == "cold":
            cold_cursor_cache(env)
        before = invocations(env)
        rc, crossed = run_seat(
            env, f"verify3{cache}", "--class", "verify", "--author-vendor", "xai", model="grok-latest"
        )
        assert rc == 2 and crossed["ok"] is False and crossed["error"].startswith("capacity: "), (cache, crossed)
        assert "grok-latest" in crossed["error"], (cache, crossed)
        assert invocations(env) == before and mine(reservations(env)["live"]) == [], cache

    missing = subprocess.run(
        seat_argv(env, "verify2", "--class", "verify"), capture_output=True, text=True, timeout=60, env=env, check=False
    )
    assert missing.returncode == 2 and "--author-vendor" in missing.stderr, missing.stderr
    assert "verify2" not in invocations(env)


def test_a_cold_model_cache_is_listed_only_under_the_hold(env: dict[str, str], tmp_path: Path) -> None:
    log = tmp_path / "discovery.log"
    env = {
        **env,
        "DISCOVERY_LOG": str(log),
        "ROUTE_CURSOR_MODELS_CMD": f"{sys.executable} {_script(tmp_path / 'discovery', DISCOVERY)}",
    }

    def listings() -> list[str]:
        return log.read_text().split() if log.exists() else []

    cold_cursor_cache(env)
    holder = start_seat(env, "hold5")

    assert listings() and all(line != "live=0" for line in listings()), listings()
    assert launched_model(env, "hold5") == "grok-4.7-low"

    cold_cursor_cache(env)
    before = listings()
    rc, waited = run_seat(env, "second5", "--class", "mechanical")
    assert rc == 4 and waited["error"].startswith("capacity: "), waited
    assert listings() == before and "second5" not in invocations(env)

    open_gate(env, "hold5")
    out, err = holder.communicate(timeout=90)
    assert holder.returncode == 0, out + err
