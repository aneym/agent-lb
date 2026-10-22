from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.floor.hook_dispatcher_guard_support import (
    BOOTOUT,
    DANGER,
    DROP_DB,
    RAW_RESTART,
    guard_home,
)
from tests.floor.hook_dispatcher_support import (
    SOURCE,
    run_hook,
)
from tests.floor.hook_dispatcher_support import kill_spawned_hooks as kill_spawned_hooks


def test_an_approval_record_never_lets_a_raw_restart_through(tmp_path: Path) -> None:

    home, env, hook = guard_home(tmp_path, "agent-lb-bootout-guard.sh", BOOTOUT)
    if not (Path("/opt/homebrew/bin/jq").exists() or shutil.which("jq", path=env["PATH"])):
        pytest.skip("the guard reads its input with jq")
    refs = home / ".agent-rails/lanes/orchestrator-refs"
    refs.mkdir(parents=True)
    (refs / "alex-restart-2026-10-08.md").write_text('Alex, 09:18 ET: "restart agent-lb"\n')
    (refs / "alex-notime-2026-10-08.md").write_text('Alex: "restart agent-lb"\n')
    env = env | {"HOME": str(home)}

    def run(suffix: str) -> subprocess.CompletedProcess:
        call = {"tool_name": "Bash", "tool_input": {"command": RAW_RESTART + suffix}, "hook_event_name": "PreToolUse"}
        return run_hook(hook["command"], call, env)

    for suffix in (
        "",
        " # alex-approval: ~/.agent-rails/lanes/orchestrator-refs/alex-restart-2026-10-08.md",
        " # alex-approval: ~/.agent-rails/lanes/orchestrator-refs/alex-missing-2026-10-08.md",
        " # alex-approval: ~/.agent-rails/lanes/orchestrator-refs/alex-notime-2026-10-08.md",
        " # alex-approval: ~/.agent-rails/lanes/orchestrator-refs/../orchestrator-refs/alex-restart-2026-10-08.md",
    ):
        refused = run(suffix)
        assert refused.returncode == 2, suffix
        assert "raw launchctl restart was not run" in refused.stderr, (suffix, refused.stderr)


def test_the_dangerous_command_guard_keeps_only_the_drop_floor(tmp_path: Path) -> None:

    home = tmp_path / "home"
    refs = home / ".agent-rails/lanes/orchestrator-refs"
    refs.mkdir(parents=True)
    (refs / "alex-drop-2026-10-08.md").write_text('Alex, 09:18 ET: "drop the scratch database"\n')
    env = dict(os.environ, HOME=str(home))
    root_delete = "rm -rf /"

    def run(command: str) -> subprocess.CompletedProcess:
        call = {"tool_name": "Bash", "tool_input": {"command": command}, "hook_event_name": "PreToolUse"}
        return subprocess.run(
            ["bash", str(DANGER)], input=json.dumps(call), env=env, capture_output=True, text=True, timeout=60
        )

    assert run(f"cat > notes.md <<'EOF'\nthe guard used to block {root_delete} in prose\nEOF").returncode == 0
    assert run(f"ssh studio '{root_delete}tmp/old'").returncode == 0
    blocked = run(DROP_DB)
    assert blocked.returncode == 2 and "floor: destructive data" in blocked.stderr, blocked
    assert "BLOCKED: Dangerous command" in blocked.stderr
    approved = run(DROP_DB + " # alex-approval: ~/.agent-rails/lanes/orchestrator-refs/alex-drop-2026-10-08.md")
    assert approved.returncode == 0, approved
    assert (
        run(DROP_DB + " # alex-approval: ~/.agent-rails/lanes/orchestrator-refs/alex-none-2026-10-08.md").returncode
        == 2
    )


@pytest.mark.parametrize(
    "command,rc",
    [
        ("printf '%s\\n' 'DROP TABLE accounts'", 0),
        ('echo "rm -rf /"', 0),
        ("cat > contract <<'EOF'\nDROP TABLE accounts; rm -rf /\nEOF", 0),
        ("cat > brief <<'B'\nrefuse rm -rf / and DROP TABLE accounts\nB", 0),
        ("psql -c 'DROP TABLE accounts'", 2),
        ("psql -c 'TRUNCATE TABLE accounts'", 2),
        ("psql -f {file}", 2),
        ("psql < {file}", 2),
        ("cat {file} | psql", 2),
    ],
)
def test_guard_trim_review_sql_counterexamples(tmp_path, command, rc):
    sql = tmp_path / "drop.sql"
    sql.write_text("DROP TABLE accounts;")
    result = subprocess.run(
        ["bash", str(DANGER)],
        input=json.dumps({"tool_input": {"command": command.format(file=sql)}}),
        text=True,
        capture_output=True,
    )
    assert result.returncode == rc, result.stderr


@pytest.mark.xfail(reason="Accepted residual: SQL comment obfuscation", strict=True)
def test_guard_trim_sql_comment_residual():
    result = subprocess.run(
        ["bash", str(DANGER)],
        input=json.dumps({"tool_input": {"command": "psql -c 'DROP/**/TABLE accounts;'"}}),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2


@pytest.mark.parametrize(
    "command,rc",
    [
        (RAW_RESTART, 2),
        ("sudo " + RAW_RESTART, 2),
        ("bash -c '" + RAW_RESTART + "'", 2),
        ("x=$(" + RAW_RESTART + ")", 2),
        ('echo "$(' + RAW_RESTART + ')"', 2),
        ("bash <<'EOF'\n" + RAW_RESTART + "\nEOF", 2),
        ("true && " + RAW_RESTART + " # done", 2),
        ("cat <<EOF\n$(" + RAW_RESTART + ")\nEOF", 2),
        ("cat > notes.md <<'EOF'\n" + RAW_RESTART + "\nEOF", 0),
        ("echo '" + RAW_RESTART + "'", 0),
        ("git commit -m 'guard: " + RAW_RESTART + " is refused'", 0),
        ("ssh ax42 '" + RAW_RESTART + "'", 0),
        ("echo '$(" + RAW_RESTART + ")'", 0),
        ("launchctl print gui/501/com.aneyman.agent-lb", 0),
    ],
)
def test_the_bootout_guard_reads_only_the_command_that_runs(tmp_path, command, rc):
    result = subprocess.run(
        ["bash", str(BOOTOUT)],
        input=json.dumps({"tool_input": {"command": command}}),
        text=True,
        capture_output=True,
        env={**os.environ, "HOME": str(tmp_path)},
    )
    assert result.returncode == rc, result.stderr


@pytest.mark.parametrize(
    "guard,command,approved_rc",
    [
        (DANGER, "psql -c 'DROP TABLE accounts'", 0),
        (SOURCE / "hooks/agent-lb-bootout-guard.sh", "launchctl bootout gui/501/com.aneyman.agent-lb", 2),
    ],
)
@pytest.mark.parametrize(
    "prefix,suffix,rc",
    [
        ("", " # alex-approval: {ref}", None),
        ("# alex-approval: {ref}\n", "", 2),
        ("", " x# alex-approval: {ref}", 2),
        ("", " ' # alex-approval: {ref}'", 2),
    ],
)
def test_guard_trim_approval_comment_boundary(tmp_path, guard, command, approved_rc, prefix, suffix, rc):
    rc = approved_rc if rc is None else rc
    refs = tmp_path / ".agent-rails/lanes/orchestrator-refs"
    refs.mkdir(parents=True)
    ref = refs / "alex-test-2026-10-08.md"
    ref.write_text("Alex 09:18 approved")
    cmd = prefix.format(ref=ref) + command + suffix.format(ref=ref)

    def run():
        return subprocess.run(
            ["bash", str(guard)],
            input=json.dumps({"tool_input": {"command": cmd}}),
            text=True,
            capture_output=True,
            env={**os.environ, "HOME": str(tmp_path)},
        )

    assert run().returncode == rc
    if rc == 0:
        original = refs / "original"
        ref.rename(original)
        ref.symlink_to(original)
        assert run().returncode == 2
