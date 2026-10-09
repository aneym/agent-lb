"""kill-scope-guard.py at its hook boundary: a PreToolUse payload on stdin, exit 2 and a BLOCKED line to deny.

factory-operations 60 (2026-10-07 23:55 ET): a lead's `pgrep -f 'no:cacheprovider' | xargs kill` SIGTERMed three other
agents' pytest runs. The process table is a recorded fake (KILL_GUARD_PS) so the guard's own selection, session walk
and decision run for real; only `ps` is replaced. Session 100 is this agent (Claude 100, its shell 101, its pytest
102); session 200 is another agent with its own pytest 202.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[2] / "config/coding-agents/hooks/kill-scope-guard.py"
TABLE = [
    {"pid": 1, "ppid": 0, "command": "/sbin/launchd"},
    {"pid": 100, "ppid": 1, "command": "/Users/a/.local/bin/claude --session-id sess-aaaaaaaa"},
    {"pid": 101, "ppid": 100, "command": "/bin/zsh -c pytest -p no:cacheprovider tests"},
    {"pid": 102, "ppid": 101, "command": "python3 -m pytest -p no:cacheprovider tests/mine"},
    {"pid": 103, "ppid": 100, "command": "python3 hook-dispatch.py PreToolUse Bash"},
    {"pid": 200, "ppid": 1, "command": "/Users/a/.local/bin/claude --session-id sess-bbbbbbbb"},
    {"pid": 202, "ppid": 200, "command": "python3 -m pytest -p no:cacheprovider tests/theirs"},
    {"pid": 300, "ppid": 1, "command": "runner --owner sess-aaaaaaaa --job x"},
]
PKILL = "pk" "ill"


def run(tmp_path, command, session="sess-aaaaaaaa"):
    table = tmp_path / "ps.json"
    table.write_text(json.dumps(TABLE))
    env = dict(os.environ, KILL_GUARD_PS=str(table), KILL_GUARD_SELF="103")
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": session}
    return subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload), capture_output=True, text=True,
                          env=env, timeout=30)


@pytest.mark.parametrize("command", [
    "pgrep -f 'no:cacheprovider' | xargs kill",  # the incident: 102 is ours, 202 is another agent's
    PKILL + " -f claude",
    PKILL + " -f pytest",
    "kill $(pgrep -f cacheprovider)",
    "kill -9 `pgrep -f tests/theirs`",
    "bash -c \"" + PKILL + " -f pytest\"",
    "cd /tmp && pgrep -f pytest | xargs -n1 kill -TERM",
    "killall python3",
])
def test_a_pattern_kill_that_reaches_another_session_is_denied(tmp_path, command):
    result = run(tmp_path, command)
    assert result.returncode == 2, result
    assert result.stderr.startswith("BLOCKED (floor: other agents' running work"), result.stderr
    assert "202" in result.stderr or "200" in result.stderr, result.stderr


@pytest.mark.parametrize("command", [
    "pgrep -f tests/mine | xargs kill",  # only this session's pytest
    "kill $(pgrep -f 'runner --owner sess-aaaaaaaa')",  # carries this session's marker
    PKILL + " -P $$",
    PKILL + " -f no-such-process-anywhere",
    "cat > notes.md <<'EOF'\n" + PKILL + " -f claude\npgrep -f pytest | xargs kill\nEOF",
    "echo '" + PKILL + " -f claude'",
    "git commit -m 'guard: " + PKILL + " -f claude is refused'",
    "ssh ax42 '" + PKILL + " -f pytest'",
    "pgrep -fl pytest",
    "kill 202",
])
def test_own_targets_and_text_that_does_not_run_are_allowed(tmp_path, command):
    result = run(tmp_path, command)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "", result.stdout


def test_a_pattern_built_at_run_time_warns_without_blocking(tmp_path):
    result = run(tmp_path, PKILL + ' -f "$TARGET"')
    assert result.returncode == 0, result.stderr
    assert "kill-scope-guard (warn only, not blocked)" in result.stdout
