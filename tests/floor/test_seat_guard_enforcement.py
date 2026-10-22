from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[2] / "config/coding-agents/hooks"


def invoke(tmp_path, hook, tool_input, override=False):
    env = dict(
        os.environ,
        HOME=str(tmp_path),
        ROUTE_LEDGER=str(tmp_path / "dispatch.jsonl"),
        SEAT_GUARD_ALLOW_ANTHROPIC="1" if override else "0",
        ROUTING_TABLE=str(tmp_path / "routing.json"),
    )
    done = subprocess.run(
        [sys.executable, str(HOOKS / hook)],
        input=json.dumps({"tool_name": "Agent" if hook == "seat-guard.py" else "Workflow", "tool_input": tool_input}),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)["hookSpecificOutput"] if done.stdout else {}


@pytest.mark.parametrize("model", [[], "opus"])
@pytest.mark.parametrize("exempt", [False, True])
def test_agent_error_rewrites_only_unexempt_claude(tmp_path, model, exempt):
    (tmp_path / "routing.json").write_text(json.dumps({"classes": [1]}))
    prompt = "Unclassified brief." + ("\nOPUS-REASON: contract" if exempt else "")
    output = invoke(
        tmp_path,
        "seat-guard.py",
        {"subagent_type": "opus-seat", "model": model, "prompt": prompt, "name": {"invalid": True}},
    )
    if exempt:
        assert "updatedInput" not in output
    else:
        assert output["updatedInput"]["subagent_type"] == "gpt-implementer"
        assert "model" not in output["updatedInput"]
        record = json.loads((tmp_path / "dispatch.jsonl").read_text().splitlines()[-1])
        assert record["error"] and record["subagent_type"] == "gpt-implementer"


@pytest.mark.parametrize(
    "options", ["{agentType: 'opus-seat', model: 'opus'}", "options", "{...options, agentType: 'opus-seat'}"]
)
@pytest.mark.parametrize("override", [False, True])
def test_workflow_hands_are_rewritten_or_denied(tmp_path, options, override):
    script = f"await agent('Fix the parser.', {options});"
    output = invoke(tmp_path, "workflow-seat-guard.py", {"script": script}, override)
    if override:
        assert "updatedInput" not in output and output.get("permissionDecision") != "deny"
    elif options.startswith("{agentType"):
        assert "gpt-implementer" in output["updatedInput"]["script"]
        assert "model: 'opus'" not in output["updatedInput"]["script"]
    else:
        assert output["permissionDecision"] == "deny"
        assert "dynamic" in output["permissionDecisionReason"]


def test_workflow_opus_reason_and_non_claude_pass(tmp_path):
    for script in [
        "await agent('Fix the parser.\\nOPUS-REASON: contract', {agentType: 'opus-seat'});",
        "await agent('Fix the parser.', {agentType: 'gpt-implementer'});",
    ]:
        output = invoke(tmp_path, "workflow-seat-guard.py", {"script": script})
        assert "updatedInput" not in output and output.get("permissionDecision") != "deny"


@pytest.mark.parametrize(
    "tool_input", [None, {"subagent_type": "gpt-explorer", "model": []}, {"subagent_type": "opus-seat", "prompt": []}]
)
def test_unrewritable_error_passes_and_logs(tmp_path, tool_input):
    output = invoke(tmp_path, "seat-guard.py", tool_input)
    assert "updatedInput" not in output
    assert json.loads((tmp_path / "dispatch.jsonl").read_text())["action"] == "pass-through"


def test_workflow_script_path_rewrites_without_editing_source(tmp_path):
    script = "await agent('[class:explore] Read the parser.', {agentType: 'general-purpose'});"
    path = tmp_path / "flow.js"
    path.write_text(script)
    output = invoke(tmp_path, "workflow-seat-guard.py", {"scriptPath": str(path)})
    assert "gpt-explorer" in output["updatedInput"]["script"]
    assert "scriptPath" not in output["updatedInput"]
    assert path.read_text() == script


@pytest.mark.parametrize(
    "script",
    [
        "await agent('Fix the parser.');",
        "await agent('Fix the parser.', {agentType: 'opus-seat', model: selected});",
        "await agent(`Fix ${file}.`, {agentType: 'opus-seat'});",
    ],
)
def test_uncheckable_workflow_cannot_admit_claude_hands(tmp_path, script):
    output = invoke(tmp_path, "workflow-seat-guard.py", {"script": script})
    assert output["permissionDecision"] == "deny"
