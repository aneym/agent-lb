from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / "config/coding-agents/hooks/seat-budget.py"


def invoke(tmp_path: Path, payload: str, **env: str) -> str:
    result = subprocess.run(
        [sys.executable, str(HOOK)], input=payload, text=True, capture_output=True,
        env=os.environ | {"HOME": str(tmp_path), "SEAT_BUDGET_CALLS": "120"} | env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    return result.stdout


def test_subagent_budget_warns_at_threshold_and_every_forty_calls(tmp_path: Path) -> None:
    payload = json.dumps({"session_id": "session-a", "agent_id": "seat-a", "tool_name": "Bash"})
    for count in range(1, 161):
        output = invoke(tmp_path, payload)
        if count in (120, 160):
            specific = json.loads(output)["hookSpecificOutput"]
            assert specific["hookEventName"] == "PostToolUse"
            assert set(specific) == {"hookEventName", "additionalContext"}
            text = specific["additionalContext"]
            assert text == (f"seat-budget: {count} tool calls in this seat; return your HANDOFF now "
                            "(done, unverified, next slice) and let the lead re-dispatch the rest.")
            assert len(text.encode()) < 300 and "\n" not in text
        else:
            assert output == ""
    for session, agent in (("session-a", "seat-b"), ("session-b", "seat-a")):
        assert invoke(tmp_path, json.dumps({"session_id": session, "agent_id": agent})) == ""


def test_main_session_calls_never_count_or_emit(tmp_path: Path) -> None:
    for _ in range(3):
        assert invoke(tmp_path, json.dumps({"session_id": "main"}), SEAT_BUDGET_CALLS="1") == ""
    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize("payload", ["garbage", "[]", "null"])
def test_invalid_input_is_silent(tmp_path: Path, payload: str) -> None:
    assert invoke(tmp_path, payload) == ""


def test_custom_budget_and_state_failure(tmp_path: Path) -> None:
    payload = json.dumps({"session_id": "session", "agent_id": "seat"})
    assert invoke(tmp_path, payload, SEAT_BUDGET_CALLS="2") == ""
    assert "seat-budget: 2 tool calls" in invoke(tmp_path, payload, SEAT_BUDGET_CALLS="2")
    blocker = tmp_path / "blocked"
    blocker.write_text("file")
    assert invoke(tmp_path, payload, SEAT_BUDGET_STATE_DIR=str(blocker / "state")) == ""
