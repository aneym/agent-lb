

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("pin,blocked,expected", [
    ("muse-wonder-1.0", [], "muse-wonder-1.0"),
    ("muse-wonder-1.0", ["muse-wonder-*"], None),
    (None, [], None),
    ("gpt-6.1-sol", [], None),
])
def test_muse_resolution_without_catalog(tmp_path: Path, pin: str | None,
                                         blocked: list[str], expected: str | None) -> None:
    table = json.loads((ROOT / "config/coding-agents/routing-table.json").read_text())
    table["aliases"]["muse-latest"]["pinned"] = pin
    table["blocked"] = blocked
    path = tmp_path / "routing.json"
    path.write_text(json.dumps(table))
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "api_models.json").write_text('{"models": []}')
    result = subprocess.run(
        [sys.executable, str(ROOT / "clients/route"), "resolve", "muse-latest"],
        env=dict(os.environ, HOME=str(tmp_path), ROUTE_TABLE=str(path),
                 ROUTE_FIXTURE_DIR=str(fixtures), AGENT_LB_URL="http://127.0.0.1:1"),
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == (0 if expected else 2), result.stderr
    if expected:
        assert result.stdout.strip() == expected


@pytest.mark.parametrize("command,blocked", [
    ('muse exec "write one line"', True),
    ('META_API_KEY=fixture-muse muse exec "write one line"', False),
    ('META_API_KEY="" muse exec "write one line"', True),
    ('env -i muse exec "write one line"', True),
    ('bash -c \'muse exec "write one line"\'', True),
    ('echo "$(muse exec task)"', True),
    ('echo "muse exec task"', False),
    ("cat <<'EOF'\nmuse exec task\nEOF\n", False),
    ("ssh studio 'muse exec task'", False),
])
def test_muse_provider_guard(tmp_path: Path, command: str, blocked: bool) -> None:
    env = {key: value for key, value in os.environ.items() if key != "META_API_KEY"}
    env["HOME"] = str(tmp_path)
    result = subprocess.run(
        ["bash", str(ROOT / "config/coding-agents/hooks/dangerous-command-guard.sh")],
        input=json.dumps({"tool_input": {"command": command}}), env=env,
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == (2 if blocked else 0), result.stderr
    assert "fixture-muse" not in result.stdout + result.stderr
    if blocked:
        assert "Alex's own Muse Code login" in result.stderr
