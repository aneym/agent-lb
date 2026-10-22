from __future__ import annotations

import json
import os
import resource
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.floor.hook_dispatcher_guard_support import (
    DANGEROUS,
)
from tests.floor.hook_dispatcher_support import (
    DENY_MERGE,
    DISPATCH,
    DISPATCH_BASH,
    LINK_HOOK,
    MERGE_CALL,
    MERGE_HOOK,
    SOURCE,
    as_result,
    decision,
    dispatcher_home,
    installer,
    make_home,
    per_hook,
    rev_of,
    run_hook,
    write_registry,
)
from tests.floor.hook_dispatcher_support import kill_spawned_hooks as kill_spawned_hooks


def test_missing_registry_fails_closed(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".claude/hooks").mkdir(parents=True)
    shutil.copy(SOURCE / "hooks/hook-dispatch.py", home / ".claude/hooks/hook-dispatch.py")
    env = os.environ | {"HOME": str(home)}
    env.pop("HOOK_DISPATCH_REGISTRY", None)
    payload = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse"}
    pre = run_hook(f"{DISPATCH} PreToolUse 'Bash'", payload, env)
    assert pre.returncode == 2
    assert json.loads(pre.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "--hook-dispatcher off" in pre.stderr

    for event in ("Stop", "UserPromptSubmit", "PostToolUse"):
        other = run_hook(f"{DISPATCH} {event} '*'", {"hook_event_name": event}, env)
        assert other.returncode == 1 and not other.stdout and "--hook-dispatcher off" in other.stderr, event

    (home / ".claude/hooks/dispatch").mkdir()
    (home / ".claude/hooks/dispatch/registry.json").write_text(json.dumps({"entries": {"PreToolUse": {}}}))
    assert run_hook(f"{DISPATCH} PreToolUse 'Bash'", payload, env).returncode == 2


@pytest.mark.parametrize("entry", [None, [], [None], "broken"])
def test_a_damaged_entry_list_refuses(tmp_path: Path, entry: Any) -> None:
    _home, env = dispatcher_home(tmp_path, entry)
    result = run_hook(DISPATCH_BASH, MERGE_CALL, env)
    assert result.returncode == 2, result.stdout + result.stderr
    reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "not a list of hooks" in reason or "not a command hook" in reason


@pytest.mark.parametrize(
    "peer",
    [
        {"hookSpecificOutput": {"permissionDecision": []}},
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": 5,
            }
        },
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "\ud800",
            }
        },
    ],
    ids=["list-decision", "int-reason", "lone-surrogate"],
)
def test_malformed_peer_output_still_denies(tmp_path: Path, peer: dict) -> None:
    peer_hook = {"type": "command", "command": "printf %s '" + json.dumps(peer) + "'"}
    _home, env = dispatcher_home(tmp_path, [MERGE_HOOK, peer_hook])
    assert per_hook([MERGE_HOOK, peer_hook], MERGE_CALL, env) == "deny"
    result = run_hook(DISPATCH_BASH, MERGE_CALL, env)
    assert result.returncode == 2, result.stdout + result.stderr
    reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "BLOCKED: merge through the queue" in reason


def test_no_temp_file_refuses(tmp_path: Path) -> None:
    _home, env = dispatcher_home(tmp_path, [MERGE_HOOK])

    result = subprocess.run(
        ["/bin/sh", "-c", DISPATCH_BASH],
        input=json.dumps(MERGE_CALL),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0)),
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("variable", ["BASH_ENV", "BASHOPTS"])
def test_env_that_changes_a_pinned_guard_runs_it(tmp_path: Path, variable: str) -> None:
    _home, env = dispatcher_home(tmp_path, [{"type": "command", "command": LINK_HOOK}])
    if shutil.which("jq", path=env.get("PATH")) is None:
        pytest.skip("the link-cli guard reads its input with jq")
    startup = tmp_path / "startup.sh"
    startup.write_text("shopt -s xpg_echo\n")
    env[variable] = str(startup) if variable == "BASH_ENV" else "xpg_echo"

    call = {
        "tool_name": "Bash",
        "tool_input": {"command": "eval $'l\\x69nk-cli auth status'"},
        "hook_event_name": "PreToolUse",
    }
    if run_hook(LINK_HOOK, call, env).returncode != 2:
        pytest.skip(f"this bash ignores {variable} (BASHOPTS needs bash 4.1+)")
    assert decision(run_hook(DISPATCH_BASH, call, env)) == "deny"

    env.pop(variable)
    trace = tmp_path / "trace.jsonl"
    ls = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse"}
    quiet = run_hook(DISPATCH_BASH, ls, env | {"HOOK_DISPATCH_TRACE": str(trace)})
    assert quiet.returncode == 0 and not quiet.stdout
    assert json.loads(trace.read_text().splitlines()[-1])["hooks"][0]["mode"] == "skipped"


def test_a_pinned_guard_with_a_fallback_answer_runs(tmp_path: Path) -> None:
    hook = {
        "type": "command",
        "command": LINK_HOOK + ' || { printf %s \'{"decision":"block","reason":"startup failed"}\'; }',
    }
    _home, env = dispatcher_home(tmp_path, [hook])
    startup = tmp_path / "startup.sh"
    startup.write_text("exit 1\n")
    env["BASH_ENV"] = str(startup)
    call = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse"}
    assert per_hook([hook], call, env) == "deny"
    assert decision(run_hook(DISPATCH_BASH, call, env)) == "deny"


def test_a_crash_notice_survives_beside_a_json_answer(tmp_path: Path) -> None:

    crash = {"type": "command", "command": "echo 'guard crashed: synthetic' >&2; exit 1"}
    context = {
        "type": "command",
        "command": "printf %s '"
        + json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "ctx"}})
        + "'",
    }
    _home, env = dispatcher_home(tmp_path, [crash, context])
    call = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse"}
    import parity_execution as parity  # type: ignore[missing-import]

    old = parity.effect(
        "PreToolUse", [as_result(h["command"], run_hook(h["command"], call, env)) for h in (crash, context)]
    )
    new = parity.effect("PreToolUse", [as_result(DISPATCH_BASH, run_hook(DISPATCH_BASH, call, env))])

    assert old["shown"] == "Failed with non-blocking status code: guard crashed: synthetic" and old["context"] == "ctx"
    assert new == old


def test_a_damaged_entry_falls_back_to_a_good_backup(tmp_path: Path) -> None:

    home, env = dispatcher_home(tmp_path, [None])
    good = {"schema": 1, "entries": {"PreToolUse": {"Bash": [MERGE_HOOK]}}, "inproc": []}
    (home / ".claude/hooks/dispatch/registry.json.bak").write_text(json.dumps(good))
    result = run_hook(DISPATCH_BASH, MERGE_CALL, env)
    assert result.returncode == 2, result.stdout + result.stderr
    assert (
        "BLOCKED: merge through the queue"
        in json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    )


def test_a_payload_that_cannot_be_written_refuses_and_leaves_no_file(tmp_path: Path) -> None:

    _home, env = dispatcher_home(tmp_path, [MERGE_HOOK])
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    env["TMPDIR"] = str(tmp)
    call = dict(MERGE_CALL, padding="x" * 4096)
    result = subprocess.run(
        ["/bin/sh", "-c", DISPATCH_BASH],
        input=json.dumps(call),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_FSIZE, (64, 64)),
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert not [name for name in os.listdir(tmp) if name.startswith("hook-dispatch-")]


def test_sessions_on_settings_without_a_rev_keep_their_fold(tmp_path: Path) -> None:

    home, _ = make_home(tmp_path)
    install, env = installer(tmp_path, home)
    install("--hook-dispatcher", "on")
    dispatch = home / ".claude/hooks/dispatch"
    registry = json.loads((dispatch / "registry.json").read_text())

    settings = json.loads((home / ".claude/settings.json").read_text())
    text = json.dumps(settings).replace(" " + registry["rev"], "")
    (home / ".claude/settings.json").write_text(json.dumps(json.loads(text), indent=2) + "\n")
    old = {key: value for key, value in registry.items() if key != "rev"}
    (dispatch / "registry.json").write_text(json.dumps(old))
    (dispatch / "registry.json.bak").write_text(json.dumps(old))
    install("--hook-dispatcher", "on")
    assert json.loads((dispatch / "registry.legacy.json").read_text()) == old

    for name in ("registry.json", "registry.json.bak"):
        current = json.loads((dispatch / name).read_text())
        current["entries"]["PreToolUse"].pop("Bash")
        (dispatch / name).write_text(json.dumps(current))
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "gh pr merge 5"},
        "cwd": str(tmp_path),
        "hook_event_name": "PreToolUse",
    }
    assert decision(run_hook(DISPATCH_BASH, payload, env)) == "deny"


def test_an_entry_never_runs_another_folds_registry(tmp_path: Path) -> None:

    true_entry = [{"type": "command", "command": "true"}]
    r1, r2 = rev_of([DENY_MERGE]), rev_of(true_entry)
    home, env = dispatcher_home(tmp_path, true_entry)
    write_registry(home, "registry.json", r2, true_entry)
    write_registry(home, "registry.json.bak", r2, true_entry)
    assert per_hook([DENY_MERGE], MERGE_CALL, env) == "deny"
    old_session = run_hook(f"{DISPATCH_BASH} {r1}", MERGE_CALL, env)
    assert old_session.returncode == 2, old_session.stdout + old_session.stderr
    assert f"not this entry's {r1}" in json.loads(old_session.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    write_registry(home, "registry.legacy.json", None, [DENY_MERGE])
    no_rev = run_hook(DISPATCH_BASH, MERGE_CALL, env)
    assert decision(no_rev) == "deny" and "BLOCKED: merge through the queue" in no_rev.stdout

    write_registry(home, f"registry.{r1}.json", r1, [DENY_MERGE])
    assert decision(run_hook(f"{DISPATCH_BASH} {r1}", MERGE_CALL, env)) == "deny"
    assert decision(run_hook(f"{DISPATCH_BASH} {r2}", MERGE_CALL, env)) == "allow"


def test_a_registry_edited_after_its_fold_never_answers(tmp_path: Path) -> None:

    folded = [DENY_MERGE, {"type": "command", "command": "true"}]
    rev = rev_of(folded)
    home, env = dispatcher_home(tmp_path, folded)
    for name in (f"registry.{rev}.json", "registry.json", "registry.json.bak"):
        write_registry(home, name, rev, [{"type": "command", "command": "true"}])
    edited = run_hook(f"{DISPATCH_BASH} {rev}", MERGE_CALL, env)
    assert edited.returncode == 2 and "do not hash to rev" in edited.stdout, edited.stdout + edited.stderr
    write_registry(home, "registry.json", rev, folded)
    assert decision(run_hook(f"{DISPATCH_BASH} {rev}", MERGE_CALL, env)) == "deny"


def test_an_escaped_command_runs_the_pinned_guard(tmp_path: Path) -> None:

    hook = {"type": "command", "command": DANGEROUS}
    _home, env = dispatcher_home(tmp_path, [hook])
    if shutil.which("jq", path=env.get("PATH")) is None:
        pytest.skip("the inline guard reads its input with jq")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "bash").write_text('#!/bin/sh\nexec /bin/bash -O xpg_echo "$@"\n')
    (bin_dir / "bash").chmod(0o755)
    env = env | {"PATH": f"{bin_dir}:{env['PATH']}"}
    call = {"tool_name": "Bash", "tool_input": {"command": "rm -" + "rf \\x2f"}, "hook_event_name": "PreToolUse"}
    assert per_hook([hook], call, env) == "deny"
    assert decision(run_hook(DISPATCH_BASH, call, env)) == "deny"
