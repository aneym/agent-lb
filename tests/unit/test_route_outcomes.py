from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.unit.test_route_dispatch import assert_valid
from tests.unit.test_route_ladder import CANONICAL_TABLE, REPO, route

FIXTURE = REPO / "tests" / "fixtures" / "outcomes-replay"
HOOKS = REPO / "config" / "coding-agents" / "hooks"


def outcome_env(tmp_path: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("ROUTE_", "OUTCOME", "STUDIO_", "AGENT_LB_DISPATCH"))}
    return {
        **env,
        "HOME": str(tmp_path),
        "ROUTE_DECISIONS_LOG": str(FIXTURE / "route-decisions.jsonl"),
        "ROUTE_LEDGER": str(FIXTURE / "dispatch.jsonl"),
        "STUDIO_MERGE_RECEIPTS": str(FIXTURE / "receipts.jsonl"),
        "ROUTE_REQUEST_LOGS": str(FIXTURE / "request-logs.jsonl"),
        "ROUTE_OUTCOME_REVERTS": str(tmp_path / "no-reverts.jsonl"),
        "OUTCOME_EVENTS": str(tmp_path / "outcome-events.jsonl"),
        "ROUTE_OUTCOMES": str(tmp_path / "outcomes.jsonl"),
        "ROUTE_OUTCOMES_NOW": "2026-10-16T12:00:00Z",
    }


def fixture_decision(index: int) -> dict:
    return json.loads((FIXTURE / "route-decisions.jsonl").read_text().splitlines()[index])


def test_replay_scores_each_job_and_charges_repairs_to_the_root(tmp_path: Path) -> None:
    result = route(outcome_env(tmp_path), CANONICAL_TABLE, "outcomes", "replay", str(FIXTURE), "--json")
    assert result.returncode == 0, result.stderr
    document = json.loads(result.stdout)
    assert document["idempotent"] is True
    assert document["new_events"][1] == 0
    rows = {row["job_id"]: row for row in document["outcomes"]}
    assert {job: row["status"] for job, row in rows.items()} == {
        "job_retry": "accepted",
        "job_fallback": "accepted",
        "job_squash_a": "accepted",
        "job_squash_b": "accepted",
        "job_root": "rejected",
        "job_repair": "accepted",
        "job_infra": "infra_failure",
    }

    retry = rows["job_retry"]
    assert len(retry["decision_ids"]) == 2 and retry["arm_id"] == "standard"
    assert retry["cost_usd"] == 0.42 and retry["attribution_complete"] is True
    assert {part["source"] for part in retry["cost_parts"]} == {"request_logs"}
    assert {part["part"] for part in retry["cost_parts"]} == {"worker", "wrapper", "retry"}

    fallback = rows["job_fallback"]
    assert fallback["fallback"] == {"seat": "cursor-seat", "model": "grok-latest",
                                    "reason": "openai-codex usage limit (429)"}
    assert fallback["cost_usd"] is None and fallback["cost_known_usd"] == 0.03
    worker = next(part for part in fallback["cost_parts"] if part["part"] == "worker")
    assert worker == {"part": "worker", "source": "cursor_pool", "usd": None, "pool_pct": 12.5}

    squash = rows["job_squash_a"]["merge_shas"]
    assert squash == rows["job_squash_b"]["merge_shas"] == ["3333333333333333333333333333333333333333"]
    assert rows["job_squash_a"]["evidence"] != rows["job_squash_b"]["evidence"]

    root = rows["job_root"]
    assert (root["repairs"], root["escalations"]) == (1, 0)
    assert {"part": "repair", "source": "request_logs", "usd": 0.06} in root["cost_parts"]
    assert root["cost_usd"] == 0.38

    infra = rows["job_infra"]
    assert infra["infra_reason"] == "exit 75" and infra["merge_shas"] == []

    for row in document["outcomes"]:
        assert_valid(row, "route-outcome.v1.json")
    for event in document["events"]:
        assert_valid(event, "outcome-event.v1.json")


def test_ingest_twice_writes_identical_outcomes(tmp_path: Path) -> None:
    env = outcome_env(tmp_path)
    first = route(env, CANONICAL_TABLE, "outcomes", "ingest", "--since", "30d", "--write", "--json")
    assert first.returncode == 0, first.stderr
    outcomes = (tmp_path / "outcomes.jsonl").read_bytes()
    events = (tmp_path / "outcome-events.jsonl").read_bytes()
    second = route(env, CANONICAL_TABLE, "outcomes", "ingest", "--since", "30d", "--write", "--json")
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["new_events"] == 0
    assert (tmp_path / "outcomes.jsonl").read_bytes() == outcomes
    assert (tmp_path / "outcome-events.jsonl").read_bytes() == events
    built = route(env, CANONICAL_TABLE, "outcomes", "build", "--json")
    assert built.returncode == 0, built.stderr
    assert json.loads(built.stdout)["outcomes"] == [json.loads(line) for line in outcomes.decode().splitlines()]
    for line in outcomes.decode().splitlines():
        assert_valid(json.loads(line), "route-outcome.v1.json")


def run_closeout(tmp_path: Path, message: str) -> tuple[dict, list[dict]]:
    ledger = tmp_path / "dispatch.jsonl"
    events = tmp_path / "outcome-events.jsonl"
    env = {**outcome_env(tmp_path), "ROUTE_LEDGER": str(ledger), "OUTCOME_EVENTS": str(events)}
    payload = {"session_id": "lead-session", "agent_type": "gpt-implementer", "agent_name": "closeout-probe",
               "agent_id": "agent-1", "last_assistant_message": message}
    subprocess.run([sys.executable, str(HOOKS / "subagent-closeout.py")], input=json.dumps(payload), text=True,
                   env=env, capture_output=True, timeout=10, check=True)
    rows = [json.loads(line) for line in events.read_text().splitlines()] if events.exists() else []
    return json.loads(ledger.read_text().splitlines()[-1]), rows


def test_closeout_hook_emits_the_event_ingest_dedupes(tmp_path: Path) -> None:
    decision = fixture_decision(1)
    result = json.dumps({"worker_model": "gpt-6.1-sol", "worker_effort": "medium", "rc": 0,
                         "session_ids": ["sess-x"]})
    record, events = run_closeout(tmp_path, f"Done.\nDispatch: {decision['decision_id']}\n{result}")
    assert (record["job_id"], record["attempt"], record["worker_model"]) == ("job_retry", 2, "gpt-6.1-sol")
    assert [event["kind"] for event in events] == ["closeout"]
    assert events[0]["event_id"] == record["event_id"]
    assert_valid(events[0], "outcome-event.v1.json")

    env = {**outcome_env(tmp_path), "ROUTE_LEDGER": str(tmp_path / "dispatch.jsonl"),
           "STUDIO_MERGE_RECEIPTS": str(tmp_path / "no-receipts.jsonl")}
    ingested = route(env, CANONICAL_TABLE, "outcomes", "ingest", "--since", "3650d", "--json")
    assert ingested.returncode == 0, ingested.stderr
    assert json.loads(ingested.stdout)["new_events"] == 1

    infra_dir = tmp_path / "infra"
    infra_dir.mkdir()
    blocked = json.dumps({"rc": 75, "worker_model": None})
    record, events = run_closeout(infra_dir, f"seat-run gave up.\nDispatch: {decision['decision_id']}\n{blocked}")
    assert (record["ok"], record["infra_reason"], events[0]["kind"]) == (False, "exit 75", "infra")


def test_workflow_readback_keeps_each_agents_decision(tmp_path: Path) -> None:
    decision_id = fixture_decision(0)["decision_id"]
    response = {"id": "wf-1", "usage": {"input_tokens": 3, "output_tokens": 1},
                "agents": [{"label": "impl", "agentType": "gpt-implementer",
                            "prompt": f"PRIVATE BRIEF\nDispatch: {decision_id}"},
                           {"label": "judge", "agentType": "general-purpose"}]}
    subprocess.run([sys.executable, str(HOOKS / "workflow-readback.py")],
                   input=json.dumps({"tool_name": "Workflow", "tool_response": response}), text=True,
                   env={**os.environ, "FACTORY_HOME": str(tmp_path)}, capture_output=True, timeout=10, check=True)
    text = (tmp_path / "workflow-readback.jsonl").read_text()
    assert json.loads(text)["agents"] == [
        {"label": "impl", "agent_type": "gpt-implementer", "decision_id": decision_id},
        {"label": "judge", "agent_type": "general-purpose", "decision_id": None},
    ]
    assert "PRIVATE BRIEF" not in text
