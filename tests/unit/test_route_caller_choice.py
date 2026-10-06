"""The table is the default; a caller may name a rung or effort with a reason (Alex, 2026-10-05:
"we shoudl allow them if we request or we want t escalate things"), and explore needs no auditor."""

from __future__ import annotations

import json
from pathlib import Path

from tests.unit.test_route_ladder import CANONICAL_TABLE, pick, route, setup


def pools(env: dict[str, str], **updates: dict) -> None:
    path = Path(env["ROUTE_FIXTURE_DIR"]) / "api_pools.json"
    document = json.loads(path.read_text())
    for pool in document["pools"]:
        pool.update(updates.get(pool["id"].replace("-", "_"), {}))
    path.write_text(json.dumps(document))


def ledger(env: dict[str, str]) -> list[dict]:
    path = Path(env["HOME"]) / ".claude" / "logs" / "dispatch.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_explore_picks_with_every_reviewer_closed(tmp_path: Path) -> None:
    env = setup(tmp_path, codex_low=False)
    closed = {"status": "exhausted", "eligibleAccounts": 0}
    pools(env, anthropic_general=closed, openai_codex=closed, cursor_models=closed)
    selected = pick(env, CANONICAL_TABLE, "explore")
    assert (selected["rung"], selected["seat"], selected["audit"]) == ("swe2-medium", "devin-seat", None)
    # Code that is accepted still carries its reviewer.
    assert pick(env, CANONICAL_TABLE, "implement")["audit"] is not None


def test_prefer_runs_the_named_rung_and_logs_why(tmp_path: Path) -> None:
    env = setup(tmp_path, codex_low=False)
    assert pick(env, CANONICAL_TABLE, "explore")["rung"] == "sol-low"
    assert ledger(env) == []
    chosen = pick(env, CANONICAL_TABLE, "explore", "--prefer", "swe2-medium", "--reason", "needs Devin's index")
    assert (chosen["rung"], chosen["default"]) == ("swe2-medium", "sol-low")
    assert chosen["reason"].startswith("preferred by caller over sol-low")
    # A table hold (Composer's gate) is waived for the caller who names it.
    gated = pick(env, CANONICAL_TABLE, "explore", "--prefer", "composer", "--reason", "probe Composer")
    assert gated["rung"] == "composer" and gated["waived"].startswith("gated:")
    events = [event for event in ledger(env) if event["event"] == "route_override"]
    assert [(event["prefer"], event["rung"], event["default"], event["why"]) for event in events] == [
        ("swe2-medium", "swe2-medium", "sol-low", "needs Devin's index"),
        ("composer", "composer", "sol-low", "probe Composer"),
    ]


def test_effort_override_is_honored_and_logged(tmp_path: Path) -> None:
    env = setup(tmp_path, codex_low=False)
    chosen = pick(env, CANONICAL_TABLE, "explore", "--effort", "high", "--reason", "escalate: tangled call graph")
    assert (chosen["rung"], chosen["effort"], chosen["effort_default"]) == ("sol-low", "high", "low")
    [event] = ledger(env)
    assert (event["event"], event["effort"], event["effort_default"], event["why"]) == (
        "route_override", "high", "low", "escalate: tangled call graph")


def test_override_needs_a_reason_and_a_runnable_rung(tmp_path: Path) -> None:
    env = setup(tmp_path, codex_low=False)
    silent = route(env, CANONICAL_TABLE, "pick", "explore", "--prefer", "swe2-medium", "--json")
    assert silent.returncode == 3 and json.loads(silent.stdout)["error"] == "missing_reason"
    unknown = route(env, CANONICAL_TABLE, "pick", "explore", "--prefer", "nope", "--reason", "x", "--json")
    assert unknown.returncode == 3 and json.loads(unknown.stdout)["error"] == "unknown_rung"
    # A preference never runs a seat whose pool is empty.
    pools(env, devin={"status": "exhausted", "eligibleAccounts": 0})
    empty = route(env, CANONICAL_TABLE, "pick", "explore", "--prefer", "swe2-medium", "--reason", "x", "--json")
    assert empty.returncode == 2 and "pool devin exhausted" in json.loads(empty.stdout)["message"]
    assert ledger(env) == []


def test_a_preference_keeps_the_review_floor_and_logs_the_real_default(tmp_path: Path) -> None:
    env = setup(tmp_path, codex_low=False)
    # Same-maker review only when no other maker can review: Sol is open, so Opus on Claude's work is refused.
    same = route(env, CANONICAL_TABLE, "pick", "verify", "--author-vendor", "anthropic",
                 "--prefer", "opus-same-vendor", "--reason", "escalation", "--json")
    assert same.returncode == 2
    assert "same maker anthropic while sol-xhigh can review" in json.loads(same.stdout)["message"]
    # The logged default is the table's pick, not the rung the waiver opened.
    probe = pick(env, CANONICAL_TABLE, "mechanical", "--prefer", "composer", "--reason", "probe")
    assert (probe["rung"], probe["default"]) == ("composer", "swe2-medium")
    # The chain path (classes without a ladder) keeps the same error contract.
    unknown = route(env, CANONICAL_TABLE, "pick", "plan", "--prefer", "nope", "--reason", "x", "--json")
    assert unknown.returncode == 3 and json.loads(unknown.stdout)["error"] == "unknown_rung"
