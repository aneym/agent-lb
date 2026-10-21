#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
hook = root / "config/coding-agents/hooks/gh-write-guard.py"
env = {k: v for k, v in os.environ.items() if k not in {"GH_TOKEN", "GITHUB_TOKEN", "GH_APP", "GH_APP_FALLBACK"}}
env["GH_APP"] = "off"
blocked = [
    "gh run cancel 1",
    "gh api -X POST repos/x/y/dispatches",
    "gh workflow run ci",
    "gh workflow enable ci",
    "gh workflow disable ci",
    "gh run rerun 1",
    "gh run delete 1",
    "gh pr create",
    "gh pr merge 1",
    "gh issue comment 1 --body text",
    "gh release upload v1 file",
    "gh repo delete x/y",
    "gh secret set x",
    "gh variable delete x",
    "gh api repos/x/y -f a=b",
    "gh api --method=DELETE repos/x/y",
    "gh api -XPATCH repos/x/y",
    "env GH_APP=off gh run cancel 1",
    "/opt/homebrew/bin/gh run cancel 1",
    "bash -c 'gh run cancel 1'",
    "gh -R x/y run cancel 1",
    "GH_APP=on GH_APP_FALLBACK=off /opt/homebrew/bin/gh run cancel 1",
    "GH_TOKEN=github_pat_fake gh run cancel 1",
]
for command in blocked + ["gh run view 1 --json status", "gh api repos/x/y", "GH_TOKEN=ghs_fixture gh run cancel 1"]:
    result = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
        env=env,
        text=True,
        capture_output=True,
    )
    expected = 2 if command in blocked else 0
    assert result.returncode == expected, (command, result.returncode, result.stderr)
    if expected:
        assert "Alex's credentials" in result.stderr and "GH_APP=on" in result.stderr
print(f"PASS Claude gh credential floor: {len(blocked)} writes blocked; reads and App token allowed")
