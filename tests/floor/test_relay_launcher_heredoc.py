import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

AGENTS = Path(__file__).resolve().parents[2] / "config/coding-agents/agents"
BLOCK = re.compile(r"^```(?:sh|bash)\n(.*?)^```", re.S | re.M)
LAUNCHERS = sorted(path.name for path in AGENTS.glob("*.md") if "<<'CONTRACT" in path.read_text())

ROUTE = """#!/usr/bin/env python3
import sys
args = sys.argv[1:]
if args[:1] == ["resolve"] and "--json" not in args:
    print("gpt-6.1-sol")
else:
    print('{"effort": "low", "job": "probe-job", "model": "gpt-6.1-sol"}')
"""
FORWARD = """import os, sys
open(os.environ["CONTRACT_OUT"], "w").write(sys.stdin.read())
"""


def launcher_block(name: str) -> tuple[str, str]:
    block = next(b for b in BLOCK.findall((AGENTS / name).read_text()) if "<<'CONTRACT" in b)
    head, _, tail = block.partition("<<'")
    body = tail.split("\n", 1)[1].rsplit("CONTRACT_EOF", 1)[0]
    head = re.sub(r"<[A-Za-z][^<>\n]*>", "p", head).replace("[--write] ", "")
    return head + "<<'" + tail, body


@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh is the seat Bash shell")
@pytest.mark.parametrize("name", LAUNCHERS)
def test_launcher_contract_reaches_forwarder_when_tmp_is_read_only(name, tmp_path):
    script, body = launcher_block(name)
    home = tmp_path / "home"
    hooks = home / ".agents/policy/coding-agents/hooks"
    bin_dir = home / ".agent-lb/bin"
    for directory in (hooks, bin_dir, tmp_path / "session-tmp", tmp_path / "p"):
        directory.mkdir(parents=True)
    (hooks / "forward-contract.py").write_text(FORWARD)
    (bin_dir / "route").write_text(ROUTE)
    (bin_dir / "route").chmod(0o755)
    out = tmp_path / "contract.txt"
    python_dir = os.path.dirname(shutil.which("python3") or "/usr/bin/python3")
    env = {
        "HOME": str(home),
        "PATH": f"{bin_dir}:{python_dir}:/usr/bin:/bin",
        "TMPDIR": str(tmp_path / "session-tmp"),
        "TMPPREFIX": str(tmp_path / "read-only-tmp/zsh"),
        "RUN": str(tmp_path / "p"),
        "CONTRACT_OUT": str(out),
    }

    result = subprocess.run(
        ["zsh", "-f", "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60
    )

    assert out.is_file(), result.stderr
    assert out.read_text() == body
