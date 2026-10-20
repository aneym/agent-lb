#!/usr/bin/env python3
import json
import os
import subprocess
from pathlib import Path

hook = Path(__file__).resolve().parents[1] / "hooks/dangerous-command-guard.sh"
env = {k: v for k, v in os.environ.items() if k not in ("RAILWAY_TOKEN", "RAILWAY_API_TOKEN")}
cases = [
    ("railway ssh", False),
    ("npx railway ssh", False),
    ("npx --yes @railway/cli ssh", False),
    ("bunx @railway/cli ssh", False),
    ("/opt/homebrew/bin/railway ssh", False),
    ("true && railway ssh", False),
    ("true; railway ssh", False),
    ("printf x | railway ssh", False),
    ('echo "$(railway ssh)"', False),
    ("RAILWAY_TOKEN= railway ssh", False),
    ("RAILWAY_TOKEN=dummy true; railway ssh", False),
    ("RAILWAY_API_TOKEN=dummy railway status", True),
    ("RAILWAY_TOKEN=dummy npx railway status", True),
    ("railway --version", True),
    ("railway help", True),
    ("railway --help", True),
    ("echo railway ssh", True),
]
for command, allowed in cases:
    result = subprocess.run(
        ["bash", str(hook)],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
        text=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == (0 if allowed else 2), (command, result.returncode, result.stderr)
    if not allowed:
        assert "kept floor: Alex's own Railway login" in result.stderr, (command, result.stderr)
for key in ("RAILWAY_TOKEN", "RAILWAY_API_TOKEN"):
    result = subprocess.run(
        ["bash", str(hook)],
        input=json.dumps({"tool_input": {"command": "railway status"}}),
        text=True,
        capture_output=True,
        env={**env, key: "dummy"},
    )
    assert result.returncode == 0, result.stderr
print(f"PASS: {len(cases) + 2} Railway hook floor cases; no Railway process executed")
