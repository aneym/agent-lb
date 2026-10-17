from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.unit.test_route_ladder import CANONICAL_TABLE, route
from tests.unit.test_route_outcomes import FIXTURE, outcome_env


@pytest.fixture
def replay(tmp_path: Path):
    env = outcome_env(tmp_path)
    table = json.loads(CANONICAL_TABLE.read_text())
    table["standards"]["implement"]["arm_id"] = "standard"
    table["standards"]["mechanical"]["arm_id"] = "standard"
    policy = tmp_path / "routing-table.json"
    policy.write_text(json.dumps(table))
    decisions = [json.loads(line) for line in (FIXTURE / "route-decisions.jsonl").read_text().splitlines()]
    for decision in decisions:
        decision["eligible_arms"] = ["standard"]
    decisions_path = tmp_path / "decisions.jsonl"
    decisions_path.write_text("".join(json.dumps(row) + "\n" for row in decisions))
    env["ROUTE_DECISIONS_LOG"] = str(decisions_path)
    result = route(env, policy, "outcomes", "ingest", "--write", "--json")
    assert result.returncode == 0, result.stderr
    return env, policy


def build(replay):
    env, policy = replay
    result = route(env, policy, "outcomes", "build", "--write", "--json")
    assert result.returncode == 0, result.stderr
    return {row["job_id"]: row for row in json.loads(result.stdout)["outcomes"]}


@pytest.mark.parametrize("override", [True, False])
def test_build_writes_rows_read_by_standings(replay, override):
    env, policy = replay
    if not override:
        env.pop("ROUTE_OUTCOMES")
    rows = build(replay)
    path = (Path(env["ROUTE_OUTCOMES"]) if override else
            Path(env["HOME"]) / ".agent-lb/audits/routing/outcomes.jsonl")
    written = {row["job_id"]: row for row in map(json.loads, path.read_text().splitlines())}
    assert written == rows
    assert rows["job_squash_a"]["outcome"] == "accepted"
    result = route(env, policy, "standings", "--since", "3650d", "--json")
    assert result.returncode == 0, result.stderr
    arms = json.loads(result.stdout)["classes"]["implement"]["arms"]
    standard = next(arm for arm in arms if arm["arm_id"] == "standard")
    assert standard["n"] == 3
    assert standard["accepted"] == 2
    mechanical = json.loads(result.stdout)["classes"]["mechanical"]["arms"]
    standard = next(arm for arm in mechanical if arm["arm_id"] == "standard")
    assert standard["n"] == standard["accepted"] == 2


@pytest.mark.parametrize("cost", [0.1, 0, None])
def test_eval_seat_joins_cost_and_distinguishes_unknown(replay, tmp_path, cost):
    env, policy = replay
    decision_id = "dcn_01K7SQASHA0000000000000000"
    seat = "eval.01k7sqasha0000000000000000"
    logs = [json.loads(line) for line in (FIXTURE / "request-logs.jsonl").read_text().splitlines()]
    for row in logs:
        if row["caller_seat"] == decision_id:
            row["caller_seat"] = seat
            if row["model"] == "gpt-6.1-sol":
                row.update(tokens=100, cost_usd=cost)
    logs_path = tmp_path / "request-logs.jsonl"
    logs_path.write_text("".join(json.dumps(row) + "\n" for row in logs))
    env["ROUTE_REQUEST_LOGS"] = str(logs_path)
    row = build(replay)["job_squash_a"]
    assert row["cost_usd"] == (0.11 if cost else None)
    assert row["cost_known"] is bool(cost)
    assert row["cost_known_usd"] == (0.11 if cost else 0.01)
    result = route(env, policy, "eval-seat", decision_id)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == seat
