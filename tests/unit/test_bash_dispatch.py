"""bash-dispatch: one PreToolUse(Bash) hook process in place of ~15, same decisions (2026-10-07 offload)."""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DISPATCH = ROOT / "config" / "coding-agents" / "hooks" / "bash-dispatch.py"
POLICY = ROOT / "config" / "coding-agents" / "install-policy.py"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def hook(tmp_path: Path, name: str, body: str, timeout: float | None = None) -> dict:
    script = tmp_path / name
    script.write_text("#!/bin/sh\n" + body + "\n")
    script.chmod(0o755)
    entry = {"type": "command", "command": f'"{script}"'}
    if timeout:
        entry["timeout"] = timeout
    return entry


def dispatch(tmp_path: Path, hooks: list[dict], command: str = "ls", raw: str | None = None, direct: list | None = None):
    listed = tmp_path / "bash-hooks.json"
    listed.write_text(json.dumps(hooks))
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": direct or []}]}}))
    payload = raw if raw is not None else json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    env = {**os.environ, "BASH_DISPATCH_LIST": str(listed), "BASH_DISPATCH_SETTINGS": str(settings)}
    return subprocess.run([sys.executable, str(DISPATCH)], input=payload, capture_output=True, text=True, env=env,
                          timeout=60)


def test_any_block_blocks_with_every_blocker_message(tmp_path):
    hooks = [hook(tmp_path, "a", "echo first >&2; exit 2"), hook(tmp_path, "b", "exit 0"),
             hook(tmp_path, "c", "echo '{\"hookSpecificOutput\":{\"updatedInput\":{\"command\":\"x\"}}}'"),
             hook(tmp_path, "d", "echo second >&2; exit 2")]
    done = dispatch(tmp_path, hooks)
    assert done.returncode == 2
    assert done.stderr == "first\nsecond\n"


def rewrite(tmp_path: Path, name: str, command: str) -> dict:
    out = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {"command": command}}})
    return hook(tmp_path, name, f"cat >/dev/null; echo '{out}'")


def test_box_offload_rewrite_wins_over_rtk(tmp_path):
    rtk, box = rewrite(tmp_path, "rtk", "rtk pytest"), rewrite(tmp_path, "box-offload-hook", "box-exec -- pytest")
    rtk["command"] += " # rtk hook claude"
    done = dispatch(tmp_path, [rtk, box], "pytest")
    assert done.returncode == 0
    assert json.loads(done.stdout)["hookSpecificOutput"]["updatedInput"]["command"] == "box-exec -- pytest"


def test_deny_wins_over_a_rewrite(tmp_path):
    deny = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny"}})
    done = dispatch(tmp_path, [rewrite(tmp_path, "r", "y"), hook(tmp_path, "d", f"echo '{deny}'")])
    assert json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_a_timed_out_hook_does_not_block(tmp_path):
    done = dispatch(tmp_path, [hook(tmp_path, "slow", "sleep 5; exit 2", timeout=0.5)])
    assert (done.returncode, done.stdout) == (0, "")


def test_a_missing_list_refuses_every_call(tmp_path):
    env = {**os.environ, "BASH_DISPATCH_LIST": str(tmp_path / "absent.json")}
    done = subprocess.run([sys.executable, str(DISPATCH)], input="{}", capture_output=True, text=True, env=env)
    assert done.returncode == 2
    assert "install-policy.py" in done.stderr


def test_a_hook_still_registered_directly_is_not_run_twice(tmp_path):
    marker = tmp_path / "ran"
    entry = hook(tmp_path, "once", f"touch {marker}")
    dispatch(tmp_path, [entry], direct=[entry])
    assert not marker.exists()


@pytest.mark.parametrize("raw", ["not json", json.dumps({"tool_name": "Bash", "tool_input": {"cmd": "x"}})])
def test_an_unreadable_payload_runs_every_gated_hook(tmp_path, raw):
    entry = hook(tmp_path, "link-cli-guard.sh", "echo seen >&2; exit 2")
    assert dispatch(tmp_path, [entry], raw=raw).returncode == 2


@pytest.mark.parametrize("marker, hits, misses", [
    ("link-cli-guard.sh", ["link-cli auth status", "lin'k'-cli x"], ["ls"]),
    ("no-direct-merge.py", ["gh pr merge 1", 'gh pr me"rge" 1', "echo $'\\x6d'"], ["git status"]),
    ("railway-vars-guard.sh", ["railway variables"], ["git log"]),
    ("stash-guard", ["git stash"], ["git status"]),
    ("rm-cd-rewrite.py", ["cd /tmp && rm x"], ["rm x", "cd /tmp"]),
    ("wide-scan-guard.sh", ["grep -r x /", "g'r'ep -r x ~", "find / -name x"], ["ls -la"]),
    ("agent-lb-bootout-guard.sh", ["launchctl kickstart gui/501/com.aneyman.agent-lb"], ["launchctl list"]),
])
def test_gates_skip_a_guard_only_when_its_trigger_word_is_absent(marker, hits, misses):
    module = load(DISPATCH, "bash_dispatch")
    for command, want in [(c, True) for c in hits] + [(c, False) for c in misses]:
        payload = {"tool_name": "Bash", "tool_input": {"command": command}}
        assert module.needed(f"/x/{marker}", json.dumps(payload), payload) is want, command


def test_unknown_hooks_always_run():
    module = load(DISPATCH, "bash_dispatch")
    payload = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
    assert module.needed("rtk hook claude", json.dumps(payload), payload) is True
    assert module.needed("/x/box-offload-hook", json.dumps(payload), payload) is True


GUARD_FAST_PATHS = {
    # Each gate copies its guard's first check; the guard must still start that way.
    "~/.agent-rails/factory-runtime/bin/rm-dynamic-deny": 'if "rm" not in raw and "\\\\u" not in raw:',
    "~/.agent-rails/factory-runtime/bin/stash-guard": 'if "stash" not in raw:',
    "~/factory/bin/desktop-guard": None,  # TRIGGERS tuple, compared below
    "~/.claude/hooks/no-direct-merge.py": 'if "merge" not in command:',
    "~/.claude/hooks/link-cli-guard.sh": "grep -q 'link-cli' || exit 0",
}


@pytest.mark.parametrize("path, text", GUARD_FAST_PATHS.items())
def test_installed_guards_still_start_with_the_fast_path_their_gate_copies(path, text):
    guard = Path(os.path.expanduser(path))
    if not guard.exists():
        pytest.skip(f"{path} is not installed here")
    source = guard.read_text()
    if text is None:
        module = load(DISPATCH, "bash_dispatch")
        found = re.search(r"^TRIGGERS = (\(.*?\))\n", source, re.S | re.M)
        assert found and ast.literal_eval(found.group(1)) == module.DESKTOP_TRIGGERS
    else:
        assert text in source


def test_install_folds_bash_hooks_into_the_list_and_uninstall_restores_them():
    policy = load(POLICY, "install_policy")
    a = {"type": "command", "command": "guard-a", "timeout": 5}
    b = {"type": "command", "command": "guard-b"}
    other = {"matcher": "Read", "hooks": [{"type": "command", "command": "read-hook"}]}
    settings = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [a]}, other, {"matcher": "Bash", "hooks": [b]}]}}
    folded, listed = policy.fold_bash_hooks(settings, [], uninstall=False)
    assert listed == [a, b]
    bash = [g for g in folded["hooks"]["PreToolUse"] if g.get("matcher") == "Bash"]
    assert bash == [{"matcher": "Bash", "hooks": [policy.BASH_DISPATCH_HOOK]}]
    assert other in folded["hooks"]["PreToolUse"]
    again, relisted = policy.fold_bash_hooks(folded, listed, uninstall=False)
    assert (again, relisted) == (folded, listed)
    c = {"type": "command", "command": "guard-c"}
    again["hooks"]["PreToolUse"].append({"matcher": "Bash", "hooks": [c]})  # another installer adds one later
    _, grown = policy.fold_bash_hooks(again, listed, uninstall=False)
    assert grown == [a, b, c]
    restored, empty = policy.fold_bash_hooks(folded, listed, uninstall=True)
    assert empty == []
    assert [g for g in restored["hooks"]["PreToolUse"] if g.get("matcher") == "Bash"] == [
        {"matcher": "Bash", "hooks": [a, b]}]
