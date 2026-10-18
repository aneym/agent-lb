from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.floor.hook_dispatcher_support import (
    COUNTED,
    REWRITE,
    bash_groups,
    hooks_of,
    installer,
    make_home,
    parity_module,
    run_hook,
)
from tests.floor.hook_dispatcher_support import kill_spawned_hooks as kill_spawned_hooks


def test_a_fold_keeps_config_order_across_overlapping_matchers(tmp_path: Path) -> None:
    home, _ = make_home(tmp_path)
    (home / "g").mkdir()
    (home / "g/wipe.py").write_text(REWRITE % "rm -rf /")
    (home / "g/safe.py").write_text(REWRITE % "echo safe")
    settings = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "true"}]},
                {"matcher": "Bash|Write", "hooks": [{"type": "command", "command": 'python3 "$HOME/g/wipe.py"'}]},
                {"matcher": "Bash", "hooks": [{"type": "command", "command": 'python3 "$HOME/g/safe.py"'}]},
            ]
        }
    }
    (home / ".claude/settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    install, env = installer(tmp_path, home)
    call = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse"}

    def last_rewrite(hooks: dict) -> str | None:
        last = None
        for group in bash_groups(hooks):
            for hook in group["hooks"]:
                out = run_hook(hook["command"], call, env).stdout
                specific = (json.loads(out) if out.strip() else {}).get("hookSpecificOutput") or {}
                if "updatedInput" in specific:
                    last = specific["updatedInput"]["command"]
        return last

    assert last_rewrite(hooks_of(home)) == "echo safe"
    install("--hook-dispatcher", "on")
    assert last_rewrite(hooks_of(home)) == "echo safe"


@pytest.mark.parametrize(
    "matcher, tool, event, expected",
    [
        (None, "Bash", "PreToolUse", True),
        ("", "Read", "PreToolUse", True),
        ("*", "Bash", "PreToolUse", True),
        ("Bash", "Bash", "PreToolUse", True),
        ("Bash", "Read", "PreToolUse", False),
        ("Edit|Write", "Write", "PreToolUse", True),
        ("Edit, Write", "Write", "PostToolUse", True),
        ("Task", "Agent", "PreToolUse", True),
        ("^Bash$", "Bash", "PreToolUse", True),
        ("^Bash$", "BashOutput", "PreToolUse", False),
        ("mcp__.*", "mcp__aside__repl", "PreToolUse", True),
        (".*", "Read", "PreToolUse", True),
        ("Ba.*h", "Bash", "PreToolUse", True),
        ("^Task$", "Agent", "PreToolUse", True),
        ("(", "Bash", "PreToolUse", False),
        ("Bash", None, "UserPromptSubmit", True),
        ("Read", "Bash", "Stop", True),
    ],
)
def test_the_fixture_matches_tools_as_claude_code_does(matcher: Any, tool: Any, event: str, expected: bool) -> None:

    assert parity_module().matches(matcher, tool, event) is expected


@pytest.mark.parametrize(
    "first, middle, tool",
    [
        ("Bash", "Write, Bash", "Bash"),
        ("Agent", "Task", "Agent"),
        ("Agent", "Write|Task", "Agent"),
        ("Bash", "*", "Bash"),
        ("Bash", "", "Bash"),
    ],
)
def test_a_fold_keeps_order_past_a_name_list_matcher(tmp_path: Path, first: str, middle: str, tool: str) -> None:

    home, _ = make_home(tmp_path)
    (home / "g").mkdir()
    for name, text in (("a.py", "first"), ("b.py", "second"), ("c.py", "third")):
        (home / "g" / name).write_text(REWRITE % text)
    settings = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": first,
                    "hooks": [
                        {"type": "command", "command": 'python3 "$HOME/g/a.py"'},
                        {"type": "command", "command": "true"},
                    ],
                },
                {"matcher": middle, "hooks": [{"type": "command", "command": 'python3 "$HOME/g/b.py"'}]},
                {"matcher": first, "hooks": [{"type": "command", "command": 'python3 "$HOME/g/c.py"'}]},
            ]
        }
    }
    (home / ".claude/settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    install, env = installer(tmp_path, home)
    call = {"tool_name": tool, "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse"}
    parity = parity_module()

    def last_rewrite() -> str | None:
        last = None
        for group in hooks_of(home)["PreToolUse"]:
            if not parity.matches(group.get("matcher"), tool):
                continue
            for hook in group["hooks"]:
                out = run_hook(hook["command"], call, env).stdout
                specific = (json.loads(out) if out.strip() else {}).get("hookSpecificOutput") or {}
                last = specific.get("updatedInput", {}).get("command", last)
        return last

    assert last_rewrite() == "third"
    install("--hook-dispatcher", "on")
    assert last_rewrite() == "third"


def test_a_command_shared_with_a_per_hook_group_runs_once_per_call(tmp_path: Path) -> None:

    home, _ = make_home(tmp_path)
    (home / "g").mkdir()
    (home / "g/count.py").write_text(COUNTED)
    counter = tmp_path / "runs.txt"
    guard = {"type": "command", "command": f'python3 "$HOME/g/count.py" "{counter}"'}
    settings = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [guard, {"type": "command", "command": "true"}]},
                {"matcher": "^Bash$", "hooks": [guard]},
            ]
        }
    }
    (home / ".claude/settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    install, env = installer(tmp_path, home)
    call = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse"}
    parity = parity_module()

    def runs_per_call() -> int:
        counter.write_text("")
        seen: list[str] = []
        for group in hooks_of(home)["PreToolUse"]:
            if parity.matches(group.get("matcher"), "Bash"):
                seen += [hook["command"] for hook in group["hooks"] if hook["command"] not in seen]
        for command in seen:
            run_hook(command, call, env)
        return len(counter.read_text().splitlines())

    assert runs_per_call() == 1
    install("--hook-dispatcher", "on")
    assert runs_per_call() == 1
