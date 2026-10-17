from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sync-runtime.sh"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"},
    ).stdout.strip()


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    src = tmp_path / "src"
    git(tmp_path, "clone", "-q", str(origin), str(src))
    git(src, "config", "user.email", "t@example.invalid")
    git(src, "config", "user.name", "t")
    git(src, "checkout", "-q", "-b", "main")
    (src / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (src / "uv.lock").write_text("lock-1\n")
    (src / "marker.txt").write_text("v1\n")
    git(src, "add", ".")
    git(src, "commit", "-q", "-m", "v1")
    git(src, "push", "-q", "origin", "main")
    return src


def run_sync(tmp_path: Path, src: Path) -> subprocess.CompletedProcess[str]:
    uv = tmp_path / "fake-uv"
    uv.write_text("#!/bin/sh\nmkdir -p .venv/bin && touch .venv/bin/agent-lb && chmod +x .venv/bin/agent-lb\n")
    uv.chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "AGENT_LB_SYNC_SRC": str(src),
        "AGENT_LB_RUNTIME_DIR": str(tmp_path / "runtime" / "agent-lb"),
        "AGENT_LB_SYNC_UV": str(uv),
    }
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)


def runtime_marker(tmp_path: Path) -> str | None:
    path = tmp_path / "runtime" / "agent-lb" / "marker.txt"
    return path.read_text() if path.exists() else None


def test_syncs_when_source_is_clean_origin_main(tmp_path: Path, checkout: Path) -> None:
    result = run_sync(tmp_path, checkout)

    assert result.returncode == 0, result.stderr
    assert runtime_marker(tmp_path) == "v1\n"
    assert "sync ok" in (tmp_path / "runtime" / "sync.log").read_text()


@pytest.mark.parametrize("state", ["ahead", "branch", "dirty", "missing"])
def test_refuses_loudly_when_source_is_not_origin_main(tmp_path: Path, checkout: Path, state: str) -> None:
    assert run_sync(tmp_path, checkout).returncode == 0
    (checkout / "marker.txt").write_text("v2\n")
    src = checkout
    if state == "ahead":
        git(checkout, "commit", "-q", "-am", "v2")
    elif state == "branch":
        git(checkout, "checkout", "-q", "-b", "topic")
        git(checkout, "commit", "-q", "-am", "v2")
        git(checkout, "push", "-q", "origin", "topic")
    elif state == "missing":
        src = tmp_path / "nowhere"

    result = run_sync(tmp_path, src)

    assert result.returncode == 1
    assert "REFUSED" in result.stderr
    assert "sync REFUSED" in (tmp_path / "runtime" / "sync.log").read_text()
    assert runtime_marker(tmp_path) == "v1\n"
