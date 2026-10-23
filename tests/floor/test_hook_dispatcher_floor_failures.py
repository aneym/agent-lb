from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.floor.hook_dispatcher_guard_support import (
    FLOOR_LEAVES,
    REACHES_PREFILTERS,
    failing_guard_home,
)
from tests.floor.hook_dispatcher_support import (
    DISPATCH_BASH,
    SOURCE,
    decision,
    dispatcher_home,
    per_hook,
    run_hook,
    run_owned,
)
from tests.floor.hook_dispatcher_support import kill_spawned_hooks as kill_spawned_hooks


@pytest.mark.parametrize("mode", ["timeout", "crash", "missing", "malformed", "no-receipt"])
@pytest.mark.parametrize("leaf", sorted(FLOOR_LEAVES))
def test_a_floor_guard_that_fails_refuses_the_call(tmp_path: Path, leaf: str, mode: str) -> None:

    command, rel, kind = FLOOR_LEAVES[leaf]
    home, env = failing_guard_home(tmp_path, command, rel, kind, mode)
    call = {"tool_name": "Bash", "tool_input": {"command": REACHES_PREFILTERS}, "hook_event_name": "PreToolUse"}
    result = run_owned(DISPATCH_BASH, call, env, 30)
    if leaf.startswith(("workflow-relay-guard.py", "wide-scan-guard.sh")):
        if mode == "missing" and kind == "py" and not any(x in command for x in ("||", "bash -c")):
            assert result is not None and result.returncode == 2
        else:
            assert result is not None and result.returncode != 2, result
        return
    assert result is not None and result.returncode == 2, result
    reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    name = leaf.split(":")[0]
    if not (mode == "missing" and kind == "py"):
        assert f"floor guard {name}" in reason and "refused" in reason, reason
        lines = (home / ".claude/hooks/dispatch/failures.jsonl").read_text().splitlines()
        logged = json.loads(lines[-1])
        assert logged["floor"] == name and logged["outcome"] == "refused"


@pytest.mark.parametrize("mode", ["timeout", "crash", "malformed"])
def test_a_non_floor_guard_that_fails_still_fails_open_and_is_logged(tmp_path: Path, mode: str) -> None:

    command = 'python3 "$HOME/.claude/hooks/no-direct-merge.py"'
    home, env = failing_guard_home(tmp_path, command, ".claude/hooks/no-direct-merge.py", "py", mode)
    call = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse"}
    assert per_hook([{"type": "command", "command": command, "timeout": 1}], call, env) == "allow"
    result = run_owned(DISPATCH_BASH, call, env, 30)
    assert result is not None and decision(result) == "allow", result
    if mode != "malformed":
        logged = json.loads((home / ".claude/hooks/dispatch/failures.jsonl").read_text().splitlines()[-1])
        assert logged["floor"] is None and logged["outcome"] == "failed open"


def test_a_floor_guards_block_stands_under_its_fallback_wrapper(tmp_path: Path) -> None:

    command = 'python3 "$HOME/.agent-rails/factory-runtime/bin/rm-dynamic-deny" 2>/dev/null || true'
    rel = ".agent-rails/factory-runtime/bin/rm-dynamic-deny"
    home, env = failing_guard_home(tmp_path, command, rel, "py", "crash")
    (home / rel).write_text(
        "import sys\nsys.stdin.read()\nsys.stderr.write('rm-dynamic-deny: blocked removal\\n')\nsys.exit(2)\n"
    )
    call = {
        "tool_name": "Agent",
        "tool_input": {"subagent_type": "Explore", "prompt": "x"},
        "hook_event_name": "PreToolUse",
    }
    result = run_owned(DISPATCH_BASH, call, env, 30)
    assert result is not None and result.returncode == 2, result
    assert "floor guard rm-dynamic-deny" in json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


def test_a_floor_guard_allows_only_with_its_receipt(tmp_path: Path) -> None:

    hook = {"type": "command", "command": '/usr/bin/python3 "$HOME/.claude/hooks/workflow-relay-guard.py"'}
    home, env = dispatcher_home(tmp_path, [hook])
    guard = home / ".claude/hooks/workflow-relay-guard.py"
    shutil.copy(SOURCE / "hooks/workflow-relay-guard.py", guard)
    call = {"tool_name": "Workflow", "tool_input": {"script": "agent('x')"}, "hook_event_name": "PreToolUse"}
    answer = run_hook(DISPATCH_BASH, call, env)
    assert answer.returncode == 0 and "floor-ok" not in answer.stdout, answer
    assert "RELAYED-REQUEST-GUARD" in json.loads(answer.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "floor-ok" not in run_hook(hook["command"], call, env).stdout
    guard.write_text(guard.read_text().replace("print(RECEIPT)", "pass"))
    refused = run_hook(DISPATCH_BASH, call, env)
    assert refused.returncode == 0, refused
    assert "RELAYED-REQUEST-GUARD" in json.loads(refused.stdout)["hookSpecificOutput"]["additionalContext"]
