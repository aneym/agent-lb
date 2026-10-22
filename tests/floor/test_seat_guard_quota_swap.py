from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "config/coding-agents/hooks/seat-guard.py"
IMPLEMENT = "Implement the parser change."


def run_hook(
    tmp_path,
    seat,
    model="opus",
    route_body='print(\'{"mode":"codex-swap"}\')',
    prompt="Review the change.",
    lean_body=None,
    missing_target=None,
):
    home = tmp_path / "home"
    home.mkdir()
    agents = home / ".claude" / "agents"
    agents.mkdir(parents=True)
    for target in ("gpt-implementer-local", "codex-sol-local", "codex-verifier-local"):
        if target != missing_target:
            (agents / f"{target}.md").write_text("---\nmodel: sol-latest\n---\n")
    lean = tmp_path / "lean-studio"
    if lean_body:
        lean.write_text(f"#!{sys.executable}\nimport sys\n{lean_body}\n")
        lean.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    route = bin_dir / "route"
    route.write_text(
        f"#!{sys.executable}\nimport sys, time\nassert sys.argv[1:] == ['quota-mode', '--json']\n{route_body}\n"
    )
    route.chmod(0o755)
    ledger = tmp_path / "dispatch.jsonl"
    tool_input = {
        "subagent_type": seat,
        "model": model,
        "prompt": prompt,
        "description": "Review",
        "run_in_background": True,
        "name": "reviewer",
    }
    env = dict(
        os.environ,
        HOME=str(home),
        PATH=str(bin_dir),
        ROUTE_LEDGER=str(ledger),
        SEAT_GUARD_ALLOW_ANTHROPIC="1",
        LEAN_STUDIO_BIN=str(lean),
        SEAT_GUARD_AGENTS_DIR=str(agents),
    )
    done = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_name": "Agent", "tool_input": tool_input}),
        text=True,
        capture_output=True,
        env=env,
        timeout=5,
    )
    assert done.returncode == 0, done.stderr
    output = json.loads(done.stdout)["hookSpecificOutput"] if done.stdout else {}
    return done, output, tool_input, json.loads(ledger.read_text()) if ledger.exists() else {}


def test_quota_swap_rewrites_an_implementation_brief(tmp_path):
    seat, target = "sonnet-implementer", "gpt-implementer"
    _done, output, original, record = run_hook(tmp_path, seat, prompt=IMPLEMENT)
    expected = dict(original, subagent_type=target)
    expected.pop("model")
    assert output["hookEventName"] == "PreToolUse"
    assert output["permissionDecision"] == "allow"
    assert output["updatedInput"] == expected
    assert f"{seat} -> {target}" in output["additionalContext"]
    assert record["original_subagent_type"] == seat
    assert record["subagent_type"] == target


@pytest.mark.parametrize(
    ("seat", "prompt"),
    [
        ("verifier", IMPLEMENT),
        ("lab-python-verifier", IMPLEMENT),
        ("plan-reviewer", IMPLEMENT),
        ("security-reviewer", IMPLEMENT),
        ("opus-seat", "Review the change."),
        ("sonnet-implementer", "Review the change."),
    ],
)
def test_quota_swap_keeps_review_and_judgment_seats(tmp_path, seat, prompt):
    done, output, original, record = run_hook(
        tmp_path, seat, prompt=prompt, route_body="raise RuntimeError('a kept seat must not read quota')"
    )
    assert "updatedInput" not in output
    assert output.get("permissionDecision") != "deny"
    assert record["subagent_type"] == original["subagent_type"] == seat
    assert "original_subagent_type" not in record and "quota_mode" not in record
    assert " -> " not in done.stderr + output.get("additionalContext", "")


@pytest.mark.parametrize("model", ["sonnet", "claude-sonnet-5-5", "claude-opus-5-5", "haiku"])
def test_quota_swap_removes_only_opus_sonnet_override(tmp_path, model):
    _, output, _, _ = run_hook(tmp_path, "sonnet-implementer", model=model, prompt=IMPLEMENT)
    assert output["updatedInput"].get("model") == ("haiku" if model == "haiku" else None)


@pytest.mark.parametrize(
    "seat", ["fork", "Explore", "general-purpose", "codex-sol", "codex-verifier", "gpt-implementer"]
)
def test_quota_swap_leaves_unrelated_seats_unchanged(tmp_path, seat):
    done, output, _, _ = run_hook(tmp_path, seat, route_body="raise RuntimeError('must not read quota')")
    assert "updatedInput" not in output
    assert "quota mode unavailable" not in done.stderr


@pytest.mark.parametrize(
    "body",
    [
        'print(\'{"mode":"anthropic"}\')',
        'print("not JSON")',
        'print(\'{"mode":"unknown"}\')',
        'print("[]")',
        "sys.exit(1)",
        "time.sleep(4)",
    ],
)
def test_quota_mode_fail_open_and_anthropic_noop(tmp_path, body):
    done, output, _, record = run_hook(tmp_path, "sonnet-implementer", route_body=body, prompt=IMPLEMENT)
    assert "updatedInput" not in output
    assert record["subagent_type"] == "sonnet-implementer"
    if "anthropic" not in body:
        assert "fail open" in done.stderr
        assert "quota_mode_error" in record


@pytest.mark.parametrize(
    ("seat", "target"),
    [
        ("sonnet-implementer-local", "gpt-implementer-local"),
        ("opus-seat-local", None),
        ("verifier-local", None),
        ("lab-python-verifier-local", None),
    ],
)
def test_quota_swap_preserves_local_execution(tmp_path, seat, target):
    _, output, _, record = run_hook(tmp_path, seat, prompt=IMPLEMENT if target else "Review the change.")
    if target:
        assert output["updatedInput"]["subagent_type"] == target
        assert record["subagent_type"] == target
    else:
        assert "updatedInput" not in output
        assert record["subagent_type"] == seat


@pytest.mark.parametrize("seat", ["verifier-local"])
def test_missing_local_target_preserves_original_seat(tmp_path, seat):
    done, output, original, record = run_hook(
        tmp_path,
        seat,
        missing_target="codex-verifier-local",
        route_body="raise RuntimeError('missing target must bypass quota lookup')",
    )
    assert "updatedInput" not in output
    assert output.get("permissionDecision") != "deny"
    assert record["subagent_type"] == original["subagent_type"] == seat
    assert "quota_mode" not in record
    assert "quota mode unavailable" not in done.stderr


@pytest.mark.parametrize("body", ['print("[" * 100000 + "]" * 100000)', 'print("null")'])
def test_quota_mode_parse_failure_never_denies(tmp_path, body):
    done, output, _, record = run_hook(tmp_path, "sonnet-implementer", route_body=body, prompt=IMPLEMENT)
    assert "updatedInput" not in output
    assert output.get("permissionDecision") != "deny"
    assert "fail open" in done.stderr
    assert "quota_mode_error" in record


@pytest.mark.parametrize("model", ["gpt-5.6", "gpt-5.4"])
def test_quota_swap_runs_advisories_and_local_placement_warnings(tmp_path, model):
    _, output, _, record = run_hook(
        tmp_path,
        "sonnet-implementer",
        model=model,
        prompt="In worktree /repo-wt/fix, fix every bug.",
    )
    assert output["updatedInput"]["subagent_type"] == "gpt-implementer"
    assert "open-ended brief" in output["additionalContext"]
    assert "no NEEDS line" in output["additionalContext"]
    assert ("off the default ladder" if model == "gpt-5.6" else "no longer served") in output["additionalContext"]
    assert record["needs_missing"] is True
    if model == "gpt-5.6":
        assert record["explicit_models"] == [{"model": model, "source": "dispatch"}]
    local = tmp_path / "local"
    local.mkdir()
    done, output, _, record = run_hook(
        local,
        "opus-seat-local",
        model=model,
        lean_body="print('brief needs Linux, not this Mac'); sys.exit(1)",
    )
    assert done.returncode == 0
    assert output.get("permissionDecision") != "deny" and "updatedInput" not in output
    assert output["additionalContext"].count("brief needs Linux, not this Mac") == 1
    assert record["lean_studio_warning"] == "brief needs Linux, not this Mac"
    assert record["subagent_type"] == "opus-seat-local"


@pytest.mark.parametrize("seat", ["sonnet-implementer"])
def test_anthropic_mode_logs_unchanged_dispatch(tmp_path, seat):
    done, output, _, record = run_hook(tmp_path, seat, route_body='print(\'{"mode":"anthropic"}\')', prompt=IMPLEMENT)
    assert "updatedInput" not in output
    assert output.get("permissionDecision") != "deny"
    assert record["quota_mode"] == "anthropic"
    assert record["subagent_type"] == seat and "original_subagent_type" not in record
    assert "quota_mode_error" not in record and "fail open" not in done.stderr


if __name__ == "__main__":
    count = 0
    for name, test in list(globals().items()):
        if not name.startswith("test_"):
            continue
        mark = test.pytestmark[0]
        fields = mark.args[0]
        fields = tuple(field.strip() for field in fields.split(",")) if isinstance(fields, str) else fields
        for case in mark.args[1]:
            values = case if len(fields) > 1 else (case,)
            with tempfile.TemporaryDirectory() as directory:
                test(Path(directory), **dict(zip(fields, values)))
            count += 1
    print(f"{count} hook subprocess cases passed")
