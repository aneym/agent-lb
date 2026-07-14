from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts" / "install-claude-clients.sh"


def test_installer_preview_is_non_mutating(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    policy_dir = tmp_path / "policy"
    result = subprocess.run(
        [str(INSTALLER), "--print"],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "AGENT_LB_CLIENT_BIN_DIR": str(bin_dir),
            "AGENT_LB_POLICY_DIR": str(policy_dir),
        },
    )

    assert not bin_dir.exists()
    assert not policy_dir.exists()
    assert f"link {bin_dir}/cc -> {ROOT}/clients/cc" in result.stdout
    assert f"link {policy_dir}/coding-agents -> {ROOT}/config/coding-agents" in result.stdout
    assert f"mcp ccdex-worker -> {bin_dir}/ccdex-worker-mcp (user scope)" in result.stdout


def test_installer_converges_links_registration_and_uninstall(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    policy_dir = tmp_path / "policy"
    bin_dir.mkdir()
    original = bin_dir / "cc"
    original.write_text("original wrapper\n")
    calls = tmp_path / "claude-calls"
    claude = tmp_path / "claude"
    claude.write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "{calls}"\n')
    claude.chmod(0o755)
    env = {
        **os.environ,
        "AGENT_LB_CLIENT_BIN_DIR": str(bin_dir),
        "AGENT_LB_POLICY_DIR": str(policy_dir),
        "AGENT_LB_CLAUDE_BIN": str(claude),
    }

    subprocess.run([str(INSTALLER)], check=True, env=env, capture_output=True, text=True)
    subprocess.run([str(INSTALLER)], check=True, env=env, capture_output=True, text=True)

    assert (bin_dir / "cc.pre-agent-lb").read_text() == "original wrapper\n"
    for name in ("cc", "ccdex", "ccdex-worker-mcp"):
        assert (bin_dir / name).is_symlink()
        assert (bin_dir / name).resolve() == (ROOT / "clients" / name).resolve()
    assert (policy_dir / "coding-agents").is_symlink()
    assert (policy_dir / "coding-agents").resolve() == (ROOT / "config" / "coding-agents").resolve()
    assert calls.read_text().splitlines() == [
        "mcp remove --scope user ccdex-worker",
        f"mcp add --scope user ccdex-worker -- {bin_dir}/ccdex-worker-mcp",
        "mcp remove --scope user ccdex-worker",
        f"mcp add --scope user ccdex-worker -- {bin_dir}/ccdex-worker-mcp",
    ]

    subprocess.run([str(INSTALLER), "--uninstall"], check=True, env=env, capture_output=True, text=True)
    assert not any((bin_dir / name).exists() for name in ("cc", "ccdex", "ccdex-worker-mcp"))
    assert not (policy_dir / "coding-agents").exists()
    assert (bin_dir / "cc.pre-agent-lb").read_text() == "original wrapper\n"
