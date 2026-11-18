from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _run(source: Path, home: Path, mode: str) -> subprocess.CompletedProcess[str]:
    installer = source.parent.parent / "scripts/install-claude-clients.sh"
    env = dict(os.environ, AGENT_LB_USER_HOME=str(home))
    env.setdefault("AGENT_LB_CLIENT_BIN_DIR", str(home / ".local/bin"))
    result = subprocess.run(["bash", str(installer), mode], capture_output=True, text=True, timeout=300, env=env)
    if result.returncode:
        raise SystemExit(f"error: client bundle {mode} failed: {(result.stderr or result.stdout).strip()[-500:]}")
    return result


def stale_client_bundle(source: Path, home: Path) -> str | None:
    if os.environ.get("AGENT_LB_POLICY_SOURCE_REPO"):
        return None
    if not (source.parent.parent / "scripts/install-claude-clients.sh").is_file():
        return None
    state, _, bundle = _run(source, home, "--bundle-status").stdout.strip().partition(" ")
    return bundle if state == "stale" else None


def install_client_bundle(source: Path, home: Path) -> str:
    lines = _run(source, home, "--clients-only").stdout.strip().splitlines()
    return next((line for line in lines if line.startswith("activated client bundle ")), "client bundle installed")
