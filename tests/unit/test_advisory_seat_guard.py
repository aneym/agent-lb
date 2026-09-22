from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

HOOK = Path(__file__).resolve().parents[2] / "config" / "coding-agents" / "hooks" / "seat-guard.py"


def invoke(
    tmp_path: Path,
    *,
    snapshot: dict | None,
    model: str = "sonnet",
    raw_input: str | None = None,
    ledger_path: Path | None = None,
) -> tuple[dict, dict | None]:
    snapshot_path = tmp_path / "snapshot.json"
    if snapshot is not None:
        snapshot_path.write_text(json.dumps(snapshot))
    ledger = ledger_path or tmp_path / "dispatch.jsonl"
    payload = (
        raw_input
        if raw_input is not None
        else json.dumps(
            {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "implementer", "model": model, "prompt": "test"},
            }
        )
    )
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        text=True,
        capture_output=True,
        check=True,
        env=os.environ | {"LIMIT_WATCH_SNAPSHOT": str(snapshot_path), "DISPATCH_LEDGER": str(ledger)},
    )
    output = json.loads(result.stdout)["hookSpecificOutput"]
    record = json.loads(ledger.read_text()) if ledger.exists() else None
    return output, record


def valid_snapshot() -> dict:
    return {
        "polled_at": datetime.now(timezone.utc).isoformat(),
        "reachable": True,
        "providers": {"anthropic": {"usable_count": 2}},
        "fable_eligible_usable": 2,
    }


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        pytest.param(None, "missing snapshot", id="missing-snapshot"),
        pytest.param(
            {**valid_snapshot(), "providers": {"anthropic": {"usable_count": 0}}},
            "anthropic usable_count < 2",
            id="exhausted-pool",
        ),
        pytest.param(
            {**valid_snapshot(), "polled_at": (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()},
            "stale snapshot",
            id="stale-snapshot",
        ),
    ],
)
def test_capacity_states_are_advisory_and_remain_telemetry(
    tmp_path: Path, snapshot: dict | None, expected: str
) -> None:
    output, record = invoke(tmp_path, snapshot=snapshot)
    assert "permissionDecision" not in output
    assert expected in output["additionalContext"]
    assert record and record["capacity_advisory"] == expected


def test_fable_model_choice_is_advisory_and_logged(tmp_path: Path) -> None:
    output, record = invoke(tmp_path, snapshot=valid_snapshot(), model="claude-fable-5-1")
    assert "permissionDecision" not in output
    assert "pins a Fable model" in output["additionalContext"]
    assert record and record["model_advisory"] == "this dispatch pins a Fable model on a subagent"


def test_malformed_input_is_never_a_permission_denial(tmp_path: Path) -> None:
    output, record = invoke(tmp_path, snapshot=None, raw_input="not json")
    assert "permissionDecision" not in output
    assert "malformed Agent hook input" in output["additionalContext"]
    assert record is None


def test_telemetry_write_failure_is_never_a_permission_denial(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("block routing telemetry")
    output, record = invoke(tmp_path, snapshot=valid_snapshot(), ledger_path=blocker / "dispatch.jsonl")
    assert "permissionDecision" not in output
    assert "could not record routing telemetry" in output["additionalContext"]
    assert record is None


# settings.json runs this hook as `/usr/bin/python3` (3.9 on macOS). A PEP 604
# annotation evaluated at import crashed it there, so every dispatch after
# 2026-09-21 went unrecorded behind "routing telemetry unavailable".
SYSTEM_PYTHON = Path("/usr/bin/python3")


@pytest.mark.skipif(not SYSTEM_PYTHON.exists(), reason="no system python on this host")
def test_dispatch_is_recorded_under_the_interpreter_the_hook_is_installed_with(tmp_path: Path) -> None:
    ledger = tmp_path / "dispatch.jsonl"
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(valid_snapshot()))
    payload = {"tool_name": "Agent", "tool_input": {"subagent_type": "opus-seat", "prompt": "[class:verify] x"}}
    result = subprocess.run(
        [str(SYSTEM_PYTHON), str(HOOK)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=os.environ | {"LIMIT_WATCH_SNAPSHOT": str(snapshot_path), "DISPATCH_LEDGER": str(ledger)},
    )
    assert result.returncode == 0, result.stderr
    record = json.loads(ledger.read_text())
    assert record["event"] == "dispatch"
    assert record["task_class"] == "verify"
