"""Real CLI and reservation HTTP service: pool health cannot hide an actual full
lease. Existing reserve tests deliberately used an advisory-only pick contract;
this regression protects the new board item 5 contract, without mocking our code.
"""
import json
from pathlib import Path

from tests.integration.test_route_reservations import route, scenario_env, server  # noqa: F401


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
