import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / "config/coding-agents/hooks/gh-write-guard.py"


@pytest.mark.parametrize(
    ("tool", "tool_input", "expected"),
    [
        ("Bash", {"command": "cat >> STATE.md <<EOF\ngh pr merge 1\nEOF\n"}, 0),
        ("Bash", {"command": "printf 'gh api -X POST' > contract.md"}, 0),
        ("Write", {"file_path": "contract.md", "content": "gh pr merge 1"}, 0),
        ("Bash", {"command": "printf '%s' gh pr merge 1"}, 0),
        ("Bash", {"command": "echo 'sh -c gh pr merge 1'"}, 0),
        ("Bash", {"command": "cat <<'EOF'\n$(gh pr merge 1)\nEOF\n"}, 0),
        ("Bash", {"command": "cat <<-EOF\n\tgh pr merge 1\n\tEOF\n"}, 0),
        ("Bash", {"command": "cat <<A <<B\ngh pr merge 1\nA\ngh issue create\nB\n"}, 0),
        ("Bash", {"command": "printf '%s' '$(gh pr merge 1)'"}, 0),
        ("Bash", {"command": "echo gh '&&' gh pr merge 1"}, 0),
        ("Bash", {"command": "gh pr view 1"}, 0),
        ("Bash", {"command": "gh api repos/x/y/issues"}, 0),
        ("Bash", {"command": "gh pr merge 1"}, 2),
        ("Bash", {"command": "2>/dev/null gh pr merge 1"}, 2),
        ("Bash", {"command": "bash <<'EOF'\ngh pr merge 1\nEOF\n"}, 2),
        ("Bash", {"command": "printf '%s' pr merge 1"}, 0),
        ("Bash", {"command": "gh api -X POST repos/x/y/issues"}, 2),
        ("Bash", {"command": "gh api --method=PATCH repos/x/y/issues/1"}, 2),
        ("Bash", {"command": "gh api -XDELETE repos/x/y/issues/1"}, 2),
        ("Bash", {"command": "cd x && gh issue create"}, 2),
        ("Bash", {"command": "gh release create v1"}, 2),
        ("Bash", {"command": "env GH_APP=off command gh pr merge 1"}, 2),
        ("Bash", {"command": "bash -lc 'gh issue create'"}, 2),
        ("Bash", {"command": 'echo "$(gh pr merge 1)"'}, 2),
        ("Bash", {"command": "cat <<EOF\n$(gh pr merge 1)\nEOF\n"}, 2),
        ("Bash", {"command": "cat <<EOF\ngh pr view 1\nEOF\ngh issue create"}, 2),
        ("Bash", {"command": "cat <<EOF\ngh pr merge 1\nEOF\ngh pr merge 1"}, 2),
        ("Bash", {"command": "echo ok\ngh issue create"}, 2),
        ("Bash", {"command": "gh api graphql -f 'query=mutation { x }'"}, 2),
    ],
)
def test_gh_account_floor_cli(tool, tool_input, expected):
    env = {key: value for key, value in os.environ.items() if key not in {"GH_TOKEN", "GITHUB_TOKEN", "GH_APP"}}
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_name": tool, "tool_input": tool_input}),
        text=True,
        capture_output=True,
        env=env,
        timeout=15,
    )
    assert result.returncode == expected, result.stderr
    if expected == 2:
        assert "Alex's credentials" in result.stderr


@pytest.mark.parametrize("command", ["echo 'unfinished", "cat <<EOF\nno end", "echo $(date"])
def test_unparsable_command_fails_closed_without_credential_claim(command):
    env = {key: value for key, value in os.environ.items() if key not in {"GH_TOKEN", "GITHUB_TOKEN", "GH_APP"}}
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
        text=True,
        capture_output=True,
        env=env,
        timeout=15,
    )
    assert result.returncode == 2, result.stderr
    assert "could not parse" in result.stderr
    assert "Alex's credentials" not in result.stderr
