

from __future__ import annotations

import json
from pathlib import Path

from tests.unit.test_route_ladder import CANONICAL_TABLE, reopen_cursor, route, setup, table_copy


def workflow_args(env: dict[str, str], table: Path = CANONICAL_TABLE) -> dict:
    result = route(env, table, "workflow-args")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_each_seat_kind_gets_options_that_reach_its_model(tmp_path: Path) -> None:


    args = workflow_args(setup(tmp_path, codex_low=False), table_copy(tmp_path, "cursor-open", reopen_cursor))
    seats = args["seats"]
    assert args["errors"] == {}

    implement = seats["implement"]
    assert (implement["seat"], implement["model"]) == ("gpt-implementer", "gpt-6.1-sol")
    assert implement["opts"] == {"agentType": "gpt-implementer", "model": "sol-latest-medium"}
    assert implement["audit"]["opts"] == {
        "agentType": "verifier", "model": "claude-sonnet-5-5", "effort": "high"}
    assert seats["explore"]["opts"] == {"agentType": "gpt-explorer", "model": "sol-latest-low"}
    assert all(seat["vendor"] == "openai" for name, seat in seats.items() if name not in ("plan", "review"))
    assert args["agent_cap"] == 6
    assert args["quota"]["opus_stages"] == ["plan", "review", "judge", "synthesis", "orchestrator"]



    assert args["review_for"]["anthropic"]["opts"] == {"agentType": "codex-verifier"}
    for vendor, reviewer in args["review_for"].items():
        assert reviewer["vendor"] != vendor
    assert 1 <= args["agent_cap"] <= 16


def test_bridge_model_with_effort_suffix_does_not_double_it(tmp_path: Path) -> None:

    def suffix_explore_model(table: dict) -> None:
        table["aliases"]["sol-latest-low"] = table["aliases"]["sol-latest"]

        rung = table["ladders"]["interim"]["explore"][0]
        rung.update(model="sol-latest-low", pool="openai-codex")

    table = table_copy(tmp_path, "suffixed-model", suffix_explore_model)
    explore = workflow_args(setup(tmp_path, codex_low=False), table)["seats"]["explore"]
    assert explore["alias"] == "sol-latest-low"
    assert explore["opts"] == {"agentType": "gpt-explorer", "model": "sol-latest-low"}


def test_cursor_exhausted_moves_implement_to_the_sol_bridge_seat(tmp_path: Path) -> None:
    env = setup(tmp_path, codex_low=False)
    pools_file = Path(env["ROUTE_FIXTURE_DIR"]) / "api_pools.json"
    pools = json.loads(pools_file.read_text())
    for pool in pools["pools"]:
        if pool["id"] == "cursor-models":
            pool["eligibleAccounts"] = 0
    pools_file.write_text(json.dumps(pools))

    implement = workflow_args(env)["seats"]["implement"]
    assert implement["opts"] == {"agentType": "gpt-implementer", "model": "sol-latest-medium"}
    assert "HANDOFF" in implement["brief"]


def test_baseline_forwarders_get_the_picked_model_and_effort_in_the_brief(tmp_path: Path) -> None:

    baseline = table_copy(tmp_path, "baseline", lambda t: t.update(ladder="baseline"))
    args = workflow_args(setup(tmp_path, claude_eligible=1), baseline)

    audit = args["seats"]["implement"]["audit"]
    assert audit["opts"] == {"agentType": "cursor-seat", "effort": "low"}
    assert audit["brief"].startswith("Seat model: claude-sonnet-5-5-high\nSeat mode: ask\n")
    codex = [f for f in args["seats"]["explore"]["fallbacks"] if f["seat"] == "codex-sol"]
    assert args["seats"]["explore"]["opts"]["model"].startswith("sol-latest")
    assert args["review_for"]["devin"]["vendor"] != "devin"


def test_normal_quota_emits_sixteen_agent_cap(tmp_path: Path) -> None:
    env = setup(tmp_path, codex_low=False)
    path = Path(env['HOME']) / '.config/open-factory/quota.toml'
    path.parent.mkdir(parents=True)
    path.write_text('mode = "normal"\n')
    args = workflow_args(env)
    assert args['agent_cap'] == 16 and args['quota']['mode'] == 'normal'
    assert args['quota']['excluded_work'] == ['status', 'wake', 'polling', 'single-file']


def test_plan_review_judge_and_orchestrator_stages_run_opus_medium_even_under_reserve(tmp_path: Path) -> None:
    env = setup(tmp_path, claude_eligible=1, codex_low=False)
    args = workflow_args(env)
    for stage in ("plan", "review", "judge", "synthesis", "orchestrator"):
        entry = args["stages"][stage]
        assert (entry["alias"], entry["model"], entry["effort"], entry["vendor"]) == (
            "opus-latest", "claude-opus-5-5", "medium", "anthropic"), stage
        assert entry["opts"]["model"] == "claude-opus-5-5" and entry["opts"]["effort"] == "medium"
    assert args["seats"]["plan"]["opts"]["agentType"] == "planner"
    assert args["seats"]["review"]["opts"]["agentType"] == "plan-reviewer"
    assert args["review_for"]["anthropic"]["effort"] == "high"
