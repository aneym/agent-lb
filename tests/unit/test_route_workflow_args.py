"""`route workflow-args` through the real CLI and canonical table.

The agent() options a Workflow script passes must reach the picked seat and model.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.unit.test_route_ladder import CANONICAL_TABLE, reopen_cursor, route, setup, table_copy


def workflow_args(env: dict[str, str], table: Path = CANONICAL_TABLE) -> dict:
    result = route(env, table, "workflow-args")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_each_seat_kind_gets_options_that_reach_its_model(tmp_path: Path) -> None:
    # Each seat kind must head some class: Cursor's gates open, Codex on pace (the live table gates Cursor
    # while it is out of usage, 87ccaf14 and 18f90918, and a low Codex pool moves explore off Sol).
    args = workflow_args(setup(tmp_path, codex_low=False), table_copy(tmp_path, "cursor-open", reopen_cursor))
    seats = args["seats"]
    assert args["errors"] == {}

    # Cursor forwarder: the CLI model rides in the brief; the Claude wrapper stays at low effort.
    implement = seats["implement"]
    assert (implement["seat"], implement["model"]) == ("cursor-seat", "grok-4.7-medium")
    assert implement["opts"] == {"agentType": "cursor-seat", "effort": "low"}
    assert implement["brief"] == "Seat model: grok-4.7-medium\n"
    # Its reviewer is a Claude seat: model and effort override the definition's frontmatter.
    assert implement["audit"]["opts"] == {
        "agentType": "verifier", "model": "claude-sonnet-5-5", "effort": "high"}

    # GPT bridge seat: the bridge locks effort from the model name, so no separate effort.
    assert seats["explore"]["opts"] == {"agentType": "gpt-explorer", "model": "sol-latest-low"}
    read_only = [f for f in seats["explore"]["fallbacks"] if f["seat"] == "cursor-seat"]
    assert read_only and read_only[0]["brief"] == "Seat model: composer-2.5\nSeat mode: ask\n"

    assert seats["plan"]["opts"] == {"agentType": "planner", "model": "claude-opus-5-5", "effort": "high"}

    # Cross-vendor review: Claude-authored work never goes to a Claude reviewer, and the
    # fixed Codex forwarder gets no model or effort its definition would ignore.
    assert args["review_for"]["anthropic"]["opts"] == {"agentType": "codex-verifier"}
    for vendor, reviewer in args["review_for"].items():
        assert reviewer["vendor"] != vendor
    assert 1 <= args["agent_cap"] <= 16


def test_bridge_model_with_effort_suffix_does_not_double_it(tmp_path: Path) -> None:
    """The real CLI must pass a usable model when the picked alias already has effort."""
    def suffix_explore_model(table: dict) -> None:
        table["aliases"]["sol-latest-low"] = table["aliases"]["sol-latest"]
        # The canonical table runs the interim ladder, so its first explore rung is what route picks.
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
    assert implement["brief"] == ""


def test_baseline_forwarders_get_the_picked_model_and_effort_in_the_brief(tmp_path: Path) -> None:
    """Review must-fixes of 2026-10-05: a Cursor auditor keeps its picked Sonnet id, codex-sol its effort."""
    baseline = table_copy(tmp_path, "baseline", lambda t: t.update(ladder="baseline"))
    args = workflow_args(setup(tmp_path, claude_eligible=1), baseline)

    audit = args["seats"]["implement"]["audit"]
    assert audit["opts"] == {"agentType": "cursor-seat", "effort": "low"}
    assert audit["brief"].startswith("Seat model: claude-sonnet-5-5-high\nSeat mode: ask\n")
    codex = [f for f in args["seats"]["explore"]["fallbacks"] if f["seat"] == "codex-sol"]
    assert codex and (codex[0]["opts"], codex[0]["brief"]) == ({"agentType": "codex-sol"}, "Effort: medium\n")
    assert args["review_for"]["devin"]["vendor"] != "devin"
