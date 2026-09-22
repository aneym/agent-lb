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
TABLE = HOOK.parent.parent / "routing-table.json"


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
        env=os.environ
        | {"LIMIT_WATCH_SNAPSHOT": str(snapshot_path), "DISPATCH_LEDGER": str(ledger), "ROUTE_TABLE": str(TABLE)},
    )
    # An admitted dispatch with nothing to report prints nothing.
    output = json.loads(result.stdout)["hookSpecificOutput"] if result.stdout.strip() else {}
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


def test_fable_model_pinned_on_a_subagent_is_denied_and_logged(tmp_path: Path) -> None:
    output, record = invoke(tmp_path, snapshot=valid_snapshot(), model="claude-fable-5-1")
    assert output["permissionDecision"] == "deny"
    assert "No seat or subagent runs on Fable or a retired model" in output["permissionDecisionReason"]
    assert record and record["denied"] == "this dispatch pins the retired model 'claude-fable-5-1' on a subagent"


def test_subagent_type_defined_on_fable_is_denied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "implementer.md").write_text("---\nname: implementer\nmodel: claude-planner\n---\nbody\n")
    monkeypatch.setenv("SEAT_GUARD_AGENTS_DIR", str(agents))
    output, record = invoke(tmp_path, snapshot=valid_snapshot(), model="")
    assert output["permissionDecision"] == "deny"
    assert record and "defined on the retired model 'claude-planner'" in record["denied"]


def test_brief_that_pins_a_retired_codex_model_is_denied(tmp_path: Path) -> None:
    payload = json.dumps(
        {
            "tool_name": "Agent",
            "tool_input": {
                "subagent_type": "cursor-seat",
                "prompt": "Run cursor-agent --model gpt-5.6-sol on the diff and report.",
            },
        }
    )
    output, record = invoke(tmp_path, snapshot=valid_snapshot(), raw_input=payload)
    assert output["permissionDecision"] == "deny"
    assert record and record["denied"] == "the brief tells the seat to use the retired model 'gpt-5.6-sol'"


def test_brief_that_only_mentions_a_retired_model_is_admitted(tmp_path: Path) -> None:
    payload = json.dumps(
        {
            "tool_name": "Agent",
            "tool_input": {
                "subagent_type": "implementer",
                "prompt": "Replace the gpt-5.6-sol pins with route resolve sol-latest; --model gpt-6-sol is fine.",
            },
        }
    )
    output, record = invoke(tmp_path, snapshot=valid_snapshot(), raw_input=payload)
    assert "permissionDecision" not in output
    assert record and "denied" not in record


@pytest.mark.parametrize(
    "brief",
    [
        "Remove the `--model gpt-5.6-sol` pin from codex-verifier.md.",
        "Replace model: gpt-6-astra with route resolve sol-latest.",
        "The seat used to run --model gpt-5.6-terra; it no longer does.",
        "Do not use `--model gpt-5.6-sol`; resolve sol-latest instead.",
        "Never use model: gpt-6-astra for this seat.",
    ],
)
def test_brief_that_asks_to_remove_a_retired_pin_is_admitted(tmp_path: Path, brief: str) -> None:
    payload = json.dumps({"tool_name": "Agent", "tool_input": {"subagent_type": "cursor-seat", "prompt": brief}})
    output, record = invoke(tmp_path, snapshot=valid_snapshot(), raw_input=payload)
    assert "permissionDecision" not in output
    assert record and "denied" not in record


def test_negated_removal_followed_by_a_use_instruction_is_still_denied(tmp_path: Path) -> None:
    brief = "Do not remove it; use `--model gpt-5.6-sol` for this run."
    payload = json.dumps({"tool_name": "Agent", "tool_input": {"subagent_type": "cursor-seat", "prompt": brief}})
    output, record = invoke(tmp_path, snapshot=valid_snapshot(), raw_input=payload)
    assert output["permissionDecision"] == "deny"


def test_opus_dispatch_is_admitted(tmp_path: Path) -> None:
    output, record = invoke(tmp_path, snapshot=valid_snapshot(), model="opus")
    assert "permissionDecision" not in output
    assert record and "denied" not in record


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
