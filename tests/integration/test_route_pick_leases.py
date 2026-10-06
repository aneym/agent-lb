"""Real CLI and reservation HTTP service: pool health cannot hide an actual full
lease. Existing reserve tests deliberately used an advisory-only pick contract;
this regression protects the new board item 5 contract, without mocking our code.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration.test_route_reservations import route, scenario_env, server  # noqa: F401
from tests.unit.test_route_ladder import SCRIPT


def test_pick_skips_a_vendor_whose_reservation_refuses(tmp_path: Path, server: str):  # noqa: F811
    env = scenario_env(tmp_path, server, "pick-leases")
    # Put Devin first, independent of changing weekly pace, with Sol as fallback.
    table = Path(env["ROUTE_TABLE"])
    policy = json.loads(table.read_text())
    rungs = {row["id"]: row for row in policy["ladders"][policy["ladder"]]["implement"]}
    policy["ladders"][policy["ladder"]]["implement"] = [rungs["swe2-high"], rungs["sol-medium"]]
    table.write_text(json.dumps(policy))
    ledger = tmp_path / "dispatch.jsonl"
    env["ROUTE_LEDGER"] = str(ledger)
    rc, first = route(env, "pick", "implement")
    assert rc == 0 and first["seat"] == "devin-seat", first
    rc, held = route(env, "reserve", "implement", "--job", "pick-held",
                     "--prefer", "devin-seat", "--reason", "regression")
    assert rc == 0 and held["status"] == "reserved", held
    try:
        rc, refused = route(env, "reserve", "implement", "--job", "pick-refused",
                            "--prefer", "devin-seat", "--reason", "regression")
        assert rc == 75 and refused["reason"] == "all candidate capacity is reserved", refused
        rc, picked = route(env, "pick", "implement")
        assert rc == 0 and picked["seat"] == "gpt-implementer", picked
        assert any(row["rung"] == "swe2-high" and "lease capacity reserved" in row["reason"]
                   for row in picked["skipped"]), picked
        assert all(row["seat"] != "devin-seat" for row in picked["fallbacks"]), picked
        rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        assert any(row["event"] == "route_pick_lease_skip" and row["capacity_key"] == "devin"
                   for row in rows), rows
    finally:
        route(env, "release", held["reservation_id"], "--outcome", "cancelled")
    rc, recovered = route(env, "pick", "implement")
    assert rc == 0 and recovered["seat"] == "devin-seat", recovered


@pytest.mark.parametrize("snapshot", [None, {"live": []}])
def test_fixture_pick_ignores_live_holds(tmp_path: Path, server: str, snapshot: dict | None):  # noqa: F811
    """The real CLI must use fixture leases, even when the HTTP service holds Devin full.

    Guards fixture isolation and ledger ownership at the subprocess boundary;
    the live fallback test above separately owns actual lease admission.
    """
    env = scenario_env(tmp_path, server, "fixture-isolation")
    table = Path(env["ROUTE_TABLE"])
    policy = json.loads(table.read_text())
    rows = policy["ladders"][policy["ladder"]]["implement"]
    rows.sort(key=lambda row: row["id"] != "swe2-high")
    table.write_text(json.dumps(policy))
    ledger = tmp_path / "dispatch.jsonl"
    env["ROUTE_LEDGER"] = str(ledger)
    if snapshot is not None:
        (Path(env["ROUTE_FIXTURE_DIR"]) / "api_pools_reservations.json").write_text(json.dumps(snapshot))
    rc, held = route(env, "reserve", "implement", "--job", "fixture-held",
                     "--prefer", "devin-seat", "--reason", "fixture isolation")
    assert rc == 0 and held["seat"] == "devin-seat", held
    ledger.unlink(missing_ok=True)
    try:
        result = subprocess.run([sys.executable, str(SCRIPT), "pick", "implement", "--json"],
                                env=env, capture_output=True, text=True, timeout=30, check=False)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["seat"] == "devin-seat"
        assert not ledger.exists()
        assert result.stderr.count("reservation availability unknown") == (1 if snapshot is None else 0)
    finally:
        route(env, "release", held["reservation_id"], "--outcome", "cancelled")
