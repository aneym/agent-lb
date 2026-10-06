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
                "tool_input": {"subagent_type": "sonnet-implementer", "model": model, "prompt": "test"},
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


def test_a_retired_model_named_on_the_dispatch_runs_and_is_logged(tmp_path: Path) -> None:
    # Alex, 2026-10-05: "we shouldnt just block model usage, we shoudl allow them if we request".
    output, record = invoke(tmp_path, snapshot=valid_snapshot(), model="claude-fable-5-1")
    assert "permissionDecision" not in output
    assert "explicit request for a model off the default ladder (claude-fable-5-1)" in output["additionalContext"]
    assert record and record["explicit_models"] == [{"model": "claude-fable-5-1", "source": "dispatch"}]
    assert "denied" not in record


def test_a_blocked_model_is_denied_even_when_named(tmp_path: Path) -> None:
    output, record = invoke(tmp_path, snapshot=valid_snapshot(), model="claude-planner")
    assert output["permissionDecision"] == "deny"
    assert record and record["denied"] == "this dispatch pins 'claude-planner', which is no longer served"


@pytest.mark.parametrize(
    ("subagent", "model", "explicit"),
    [
        pytest.param("fable-orchestrator", "claude-fable-5-1", False, id="fable-on-its-readmitted-seat"),
        pytest.param("astra-consult", "gpt-6-astra", False, id="astra-on-its-readmitted-seat"),
        pytest.param("astra-consult", "claude-fable-5-1", True, id="fable-on-the-astra-seat"),
        pytest.param("sol-consult", "gpt-6-astra", True, id="astra-on-another-seat"),
        pytest.param("sol-consult", "astra-latest-high", True, id="astra-alias-on-another-seat"),
    ],
)
def test_readmitted_seats_use_their_own_family_without_asking(
    tmp_path: Path, subagent: str, model: str, explicit: bool
) -> None:
    # Elsewhere the same model runs only as a logged explicit request.
    payload = json.dumps({"tool_name": "Agent", "tool_input": {"subagent_type": subagent, "model": model, "prompt": "x"}})
    output, record = invoke(tmp_path, snapshot=valid_snapshot(), raw_input=payload)
    assert "permissionDecision" not in output
    assert record and ("explicit_models" in record) is explicit


@pytest.mark.parametrize("pinned", ["claude-fable-5-1", "astra-latest-high", "claude-planner"])
def test_a_definition_that_pins_an_off_ladder_model_with_nothing_asking_is_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned: str
) -> None:
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "sonnet-implementer.md").write_text(f"---\nname: sonnet-implementer\nmodel: {pinned}\n---\nbody\n")
    monkeypatch.setenv("SEAT_GUARD_AGENTS_DIR", str(agents))
    output, record = invoke(tmp_path, snapshot=valid_snapshot(), model="")
    assert output["permissionDecision"] == "deny"
    assert record and "defined on " in record["denied"] and pinned in record["denied"]


def test_a_brief_that_names_an_off_ladder_model_runs_and_is_logged(tmp_path: Path) -> None:
    def brief(model: str) -> str:
        return json.dumps({"tool_name": "Agent", "tool_input": {
            "subagent_type": "cursor-seat", "description": "escalate after two failures",
            "prompt": f"Run cursor-agent --model {model} on the diff and report."}})

    output, record = invoke(tmp_path, snapshot=valid_snapshot(), raw_input=brief("gpt-5.6-sol"))
    assert "permissionDecision" not in output
    assert record and record["explicit_models"] == [{"model": "gpt-5.6-sol", "source": "brief"}]
    assert record["why"] == "escalate after two failures"
    output, record = invoke(tmp_path, snapshot=valid_snapshot(), raw_input=brief("gpt-5.4-mini"),
                            ledger_path=tmp_path / "blocked.jsonl")
    # Prose is advisory even for a model gone upstream (a prose hit denied a verifier at 19:51Z, 2026-10-05).
    assert "permissionDecision" not in output
    assert "no longer served upstream" in output["additionalContext"]
    assert record and "denied" not in record
    assert record["explicit_models"] == [{"model": "gpt-5.4-mini", "source": "brief", "blocked": True}]


def test_brief_that_only_mentions_a_retired_model_is_admitted(tmp_path: Path) -> None:
    payload = json.dumps(
        {
            "tool_name": "Agent",
            "tool_input": {
                "subagent_type": "gpt-implementer",
                "prompt": "Replace the gpt-5.6-sol pins with route resolve sol-latest; --model gpt-6-sol is fine.",
            },
        }
    )
    output, record = invoke(tmp_path, snapshot=valid_snapshot(), raw_input=payload)
    assert "permissionDecision" not in output
    assert record and "denied" not in record and "explicit_models" not in record


@pytest.mark.parametrize(
    "brief",
    [
        "Remove the `--model gpt-5.6-sol` pin from codex-verifier.md.",
        "Replace model: gpt-6-astra with route resolve sol-latest.",
        "The seat used to run --model gpt-5.6-terra; it no longer does.",
        "Do not use `--model gpt-5.6-sol`; resolve sol-latest instead.",
        "Never use model: gpt-6-astra for this seat.",
        "Avoid model: gpt-5.6-sol here.",
    ],
)
def test_brief_that_asks_to_remove_a_retired_pin_is_admitted(tmp_path: Path, brief: str) -> None:
    # A pin the brief removes or forbids is not a request, so nothing is logged as one.
    payload = json.dumps({"tool_name": "Agent", "tool_input": {"subagent_type": "cursor-seat", "prompt": brief}})
    output, record = invoke(tmp_path, snapshot=valid_snapshot(), raw_input=payload)
    assert "permissionDecision" not in output
    assert record and "denied" not in record and "explicit_models" not in record


def test_negated_removal_followed_by_a_use_instruction_is_still_a_request(tmp_path: Path) -> None:
    brief = "Do not remove it; use `--model gpt-5.6-sol` for this run."
    payload = json.dumps({"tool_name": "Agent", "tool_input": {"subagent_type": "cursor-seat", "prompt": brief}})
    output, record = invoke(tmp_path, snapshot=valid_snapshot(), raw_input=payload)
    assert "permissionDecision" not in output
    assert record and record["explicit_models"] == [{"model": "gpt-5.6-sol", "source": "brief"}]


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
