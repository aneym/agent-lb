from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.floor.hook_dispatcher_guard_support import (
    BOOTOUT,
    DANGER,
    DANGEROUS,
    DROP_DB,
    RAILWAY,
    RAILWAY_PAYLOADS,
    RAW_RESTART,
    WIDE_SCAN,
    WIDE_SCAN_COMMANDS,
    guard_home,
    stub_bin,
    tools_without,
)
from tests.floor.hook_dispatcher_support import (
    DISPATCH_BASH,
    SOURCE,
    decision,
    dispatcher_home,
    policy_module,
    rewrite_of,
    run_hook,
    run_owned,
)
from tests.floor.hook_dispatcher_support import kill_spawned_hooks as kill_spawned_hooks


def test_the_pinned_wide_scan_guard_is_skipped_only_where_it_cannot_act(tmp_path: Path) -> None:

    hook = {"type": "command", "command": '"$HOME/.claude/hooks/wide-scan-guard.sh"'}
    home, env = dispatcher_home(tmp_path, [hook])
    installed = home / ".claude/hooks/wide-scan-guard.sh"
    shutil.copy(WIDE_SCAN, installed)
    installed.chmod(0o755)
    pins = policy_module().script_pins(SOURCE)
    assert pins["wide-scan-guard.sh"] == hashlib.sha256(installed.read_bytes()).hexdigest()
    registry_path = home / ".claude/hooks/dispatch/registry.json"
    registry_path.write_text(json.dumps(dict(json.loads(registry_path.read_text()), script_sha=pins)))
    trace = tmp_path / "trace.jsonl"
    cwds = [
        str(home / "project"),
        str(home),
        str(home / ".agent-rails/lanes/a"),
        "/Volumes/StudioExt/repos/x",
        "/Volumes/StudioExt/repos",
        "/Volumes/StudioExt/repos/x-wt",
    ]
    differ, skipped = [], set()
    for cwd in cwds:
        for command in WIDE_SCAN_COMMANDS:
            call = {
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "hook_event_name": "PreToolUse",
                "cwd": cwd,
            }
            old = run_hook(hook["command"], call, env)
            new = run_hook(DISPATCH_BASH, call, env | {"HOOK_DISPATCH_TRACE": str(trace)})
            if (decision(old), rewrite_of(old)) != (decision(new), rewrite_of(new)):
                differ.append((cwd, command, old.returncode, new.returncode, new.stdout[:200]))
            row = json.loads(trace.read_text().splitlines()[-1])
            if row["hooks"][0].get("mode") == "skipped":
                skipped.add((cwd, command))
    assert not differ, differ
    for command in (
        "rg -n foo src",
        "find . -name '*.py'",
        "ls -la",
        "rg x /tmp/project",
        "ls -la # it's fine",
        "python3 -c 'print(1)'",
    ):
        assert (str(home / "project"), command) in skipped, command


@pytest.mark.parametrize(
    "jq",
    [
        "#!/bin/sh\necho 'jq: error: something broke' >&2\nexit 5\n",
        "#!/bin/sh\necho 'sh: jq: command not found' >&2\nexit 127\n",
    ],
    ids=["jq-fails", "jq-missing"],
)
def test_the_wide_scan_guard_refuses_when_jq_fails(tmp_path: Path, jq: str) -> None:

    _home, env, hook = guard_home(tmp_path, "wide-scan-guard.sh", WIDE_SCAN)
    env = stub_bin(tmp_path, env, "jq", jq)
    call = {
        "tool_name": "Bash",
        "tool_input": {"command": "rg needle /"},
        "hook_event_name": "PreToolUse",
        "cwd": str(tmp_path),
    }
    old = run_hook(hook["command"], call, env)
    assert old.returncode == 2 and "could not read the hook input" in old.stderr, old
    new = run_owned(DISPATCH_BASH, call, env, 30)
    assert new is not None and decision(new) == "deny", new
    assert "could not read the hook input" in json.loads(new.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


def test_a_guard_missing_a_tool_it_needs_is_never_skipped(tmp_path: Path) -> None:

    home, env, hook = guard_home(tmp_path, "wide-scan-guard.sh", WIDE_SCAN)
    registry_path = home / ".claude/hooks/dispatch/registry.json"
    registry_path.write_text(
        json.dumps(dict(json.loads(registry_path.read_text()), script_sha=policy_module().script_pins(SOURCE)))
    )
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("bash", "sh", "cat", "python3", "printf", "env"):
        found = shutil.which(name, path=env["PATH"])
        if found:
            (tools / name).symlink_to(found)
    env = env | {"PATH": str(tools)}
    call = {
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "hook_event_name": "PreToolUse",
        "cwd": str(home / "project"),
    }
    assert decision(run_hook(hook["command"], call, env)) == "deny"
    result = run_owned(DISPATCH_BASH, call, env, 30)
    assert result is not None and decision(result) == "deny", result


def test_the_wide_scan_guard_refuses_when_its_scanner_fails(tmp_path: Path) -> None:

    _home, env, hook = guard_home(tmp_path, "wide-scan-guard.sh", WIDE_SCAN)
    real = shutil.which("python3", path=env["PATH"])
    assert real
    env = stub_bin(
        tmp_path,
        env,
        "python3",
        f"""#!/bin/sh
if [ "$1" = "-" ]; then echo 'Traceback (most recent call last): synthetic scanner crash' >&2; exit 1; fi
exec "{real}" "$@"
""",
    )
    call = {
        "tool_name": "Bash",
        "tool_input": {"command": "rg needle /"},
        "hook_event_name": "PreToolUse",
        "cwd": str(tmp_path),
    }
    old = run_hook(hook["command"], call, env)
    assert old.returncode == 2 and "scanner failed (python3 exit 1)" in old.stderr, old
    new = run_owned(DISPATCH_BASH, call, env, 30)
    assert new is not None and decision(new) == "deny", new
    assert "scanner failed" in json.loads(new.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


def test_a_bootout_guard_that_times_out_refuses_a_raw_restart(tmp_path: Path) -> None:

    home, env, _hook = guard_home(tmp_path, "agent-lb-bootout-guard.sh", BOOTOUT, timeout=1)
    (home / ".claude/hooks/agent-lb-bootout-guard.sh").write_text("#!/bin/bash\nexec sleep 30\n")
    call = {"tool_name": "Bash", "tool_input": {"command": RAW_RESTART}, "hook_event_name": "PreToolUse"}
    result = run_owned(DISPATCH_BASH, call, env, 30)
    assert result is not None and result.returncode == 2, result
    reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "floor guard agent-lb-bootout-guard.sh timed out" in reason, reason


def test_the_adopted_bootout_guard_refuses_input_it_cannot_read(tmp_path: Path) -> None:

    _home, env, hook = guard_home(tmp_path, "agent-lb-bootout-guard.sh", BOOTOUT)
    if not (Path("/opt/homebrew/bin/jq").exists() or shutil.which("jq", path=env["PATH"])):
        pytest.skip("the guard reads its input with jq")
    call = {"tool_name": "Bash", "tool_input": {"command": RAW_RESTART}, "hook_event_name": "PreToolUse"}
    refused = run_hook(hook["command"], call, env)
    assert refused.returncode == 2 and "raw launchctl restart of agent-lb" in refused.stderr, refused
    assert "floor: keeps the orchestrators up" in refused.stderr, refused
    assert decision(run_hook(hook["command"], dict(call, tool_input={"command": "ls"}), env)) == "allow"
    broken = subprocess.run(
        ["/bin/sh", "-c", hook["command"]],
        input="{not json " + RAW_RESTART,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert broken.returncode == 2 and "could not read the hook input" in broken.stderr, broken


def test_the_bootout_guard_refuses_a_raw_restart_when_its_matcher_fails(tmp_path: Path) -> None:

    _home, env, hook = guard_home(tmp_path, "agent-lb-bootout-guard.sh", BOOTOUT)
    jq = "/opt/homebrew/bin/jq" if Path("/opt/homebrew/bin/jq").exists() else shutil.which("jq", path=env["PATH"])
    if not jq:
        pytest.skip("the guard reads its input with jq")
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("bash", "sh", "python3", "env"):
        found = shutil.which(name, path=env["PATH"])
        if found:
            (tools / name).symlink_to(found)
    (tools / "jq").symlink_to(jq)
    env = env | {"PATH": str(tools)}
    call = {"tool_name": "Bash", "tool_input": {"command": RAW_RESTART}, "hook_event_name": "PreToolUse"}
    old = run_hook(hook["command"], call, env)
    assert old.returncode == 2 and "could not match the command" in old.stderr, old
    new = run_owned(DISPATCH_BASH, call, env, 30)
    assert new is not None and decision(new) == "deny", new
    assert "could not match the command" in json.loads(new.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert decision(run_hook(hook["command"], dict(call, tool_input={"command": "ls"}), env)) == "allow"


def test_the_pinned_railway_guard_is_skipped_only_where_it_cannot_act(tmp_path: Path) -> None:

    hook = {"type": "command", "command": '"$HOME/.claude/hooks/railway-vars-guard.sh"'}
    home, env, _hook = guard_home(tmp_path, "railway-vars-guard.sh", RAILWAY)
    registry_path = home / ".claude/hooks/dispatch/registry.json"
    registry_path.write_text(
        json.dumps(dict(json.loads(registry_path.read_text()), script_sha=policy_module().script_pins(SOURCE)))
    )
    trace = tmp_path / "trace.jsonl"
    differ, skipped = [], set()
    for tool_input in RAILWAY_PAYLOADS:
        call = {"tool_name": "Bash", "tool_input": tool_input, "hook_event_name": "PreToolUse", "cwd": str(tmp_path)}
        old = run_hook(hook["command"], call, env)
        new = run_hook(DISPATCH_BASH, call, env | {"HOOK_DISPATCH_TRACE": str(trace)})
        if decision(old) != decision(new):
            differ.append((tool_input, old.returncode, new.returncode, new.stdout[:200]))
        if json.loads(trace.read_text().splitlines()[-1])["hooks"][0].get("mode") == "skipped":
            skipped.add(json.dumps(tool_input))
    assert not differ, differ
    assert {json.dumps({"command": "ls -la"}), json.dumps({"command": "git status"})} <= skipped, skipped


def test_the_wide_scan_guard_refuses_when_cat_fails(tmp_path: Path) -> None:

    _home, env, hook = guard_home(tmp_path, "wide-scan-guard.sh", WIDE_SCAN)
    env = tools_without(tmp_path, env, "cat")
    call = {
        "tool_name": "Bash",
        "tool_input": {"command": "rg needle /"},
        "hook_event_name": "PreToolUse",
        "cwd": str(tmp_path),
    }
    assert decision(run_hook(hook["command"], call, env)) == "deny"
    new = run_owned(DISPATCH_BASH, call, env, 30)
    assert new is not None and decision(new) == "deny", new
    assert "wide-scan-guard" in json.loads(new.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("missing", ["jq", "grep"])
@pytest.mark.parametrize(
    "command", ['"$HOME/.claude/hooks/dangerous-command-guard.sh"', DANGEROUS], ids=["script", "legacy-inline"]
)
def test_the_dangerous_command_guard_refuses_when_its_reader_or_matcher_fails(
    tmp_path: Path, missing: str, command: str
) -> None:

    hook = {"type": "command", "command": command, "timeout": 20}
    home, env = dispatcher_home(tmp_path, [hook])
    shutil.copy(DANGER, home / ".claude/hooks/dangerous-command-guard.sh")
    (home / ".claude/hooks/dangerous-command-guard.sh").chmod(0o755)
    call = {"tool_name": "Bash", "tool_input": {"command": DROP_DB}, "hook_event_name": "PreToolUse"}
    healthy = run_owned(DISPATCH_BASH, call, env, 30)
    assert healthy is not None and decision(healthy) == "deny", healthy
    assert "BLOCKED: Dangerous command" in healthy.stdout
    assert decision(run_owned(DISPATCH_BASH, dict(call, tool_input={"command": "ls"}), env, 30)) == "allow"
    broken = run_owned(DISPATCH_BASH, call, tools_without(tmp_path, env, missing), 30)
    assert broken is not None and decision(broken) == "deny", broken
