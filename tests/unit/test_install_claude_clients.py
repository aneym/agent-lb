from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts" / "install-claude-clients.sh"
POLICY_INSTALLER = ROOT / "config" / "coding-agents" / "install-policy.py"
MARKER_BLOCK = (
    "<!-- agent-lb:coding-agent-routing:start -->\n"
    "## Fable/Codex Routing\n\nretired ccdex adapter\n"
    "<!-- agent-lb:coding-agent-routing:end -->\n"
)


def test_installer_preview_is_non_mutating(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    policy_dir = tmp_path / "policy"
    user_home = tmp_path / "home"
    result = subprocess.run(
        [str(INSTALLER), "--print"],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "AGENT_LB_CLIENT_BIN_DIR": str(bin_dir),
            "AGENT_LB_POLICY_DIR": str(policy_dir),
            "AGENT_LB_USER_HOME": str(user_home),
        },
    )

    assert not bin_dir.exists()
    assert not policy_dir.exists()
    assert f"link {bin_dir}/cc -> {ROOT}/clients/cc" in result.stdout
    assert f"link {policy_dir}/coding-agents -> {ROOT}/config/coding-agents" in result.stdout
    assert f"would converge managed routing configuration in {user_home}/.claude/CLAUDE.md" in result.stdout
    assert "remove retired ccdex artifacts (clients, hook, MCP registration)" in result.stdout
    assert f"link {bin_dir}/ccdex" not in result.stdout


def test_installer_converges_links_and_removes_retired_artifacts(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    policy_dir = tmp_path / "policy"
    user_home = tmp_path / "home"
    bin_dir.mkdir()
    original = bin_dir / "cc"
    original.write_text("original wrapper\n")
    (bin_dir / "ccdex").symlink_to(ROOT / "clients" / "ccdex")
    (bin_dir / "ccdex-worker-mcp").symlink_to(ROOT / "clients" / "ccdex-worker-mcp")
    calls = tmp_path / "claude-calls"
    claude = tmp_path / "claude"
    claude.write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "{calls}"\n')
    claude.chmod(0o755)
    hook = user_home / ".claude" / "hooks" / "ccdex-gpt-only.sh"
    hook.parent.mkdir(parents=True)
    hook.symlink_to(ROOT / "config" / "coding-agents" / "install-policy.py")
    codex_doc = user_home / ".codex" / "AGENTS.md"
    codex_doc.parent.mkdir(parents=True)
    codex_doc.write_text(f"# Codex\n\nkeep-codex\n\n{MARKER_BLOCK}")
    env = {
        **os.environ,
        "AGENT_LB_CLIENT_BIN_DIR": str(bin_dir),
        "AGENT_LB_POLICY_DIR": str(policy_dir),
        "AGENT_LB_CLAUDE_BIN": str(claude),
        "AGENT_LB_USER_HOME": str(user_home),
    }

    subprocess.run([str(INSTALLER)], check=True, env=env, capture_output=True, text=True)
    subprocess.run([str(INSTALLER)], check=True, env=env, capture_output=True, text=True)

    assert (bin_dir / "cc.pre-agent-lb").read_text() == "original wrapper\n"
    assert (bin_dir / "cc").is_symlink()
    assert (bin_dir / "cc").resolve() == (ROOT / "clients" / "cc").resolve()
    for name in ("ccdex", "ccdex-worker-mcp"):
        assert not (bin_dir / name).exists()
    assert not hook.exists()
    assert (policy_dir / "coding-agents").is_symlink()
    assert (policy_dir / "coding-agents").resolve() == (ROOT / "config" / "coding-agents").resolve()
    claude_doc_text = (user_home / ".claude" / "CLAUDE.md").read_text()
    assert claude_doc_text.count("agent-lb:coding-agent-routing:start") == 1
    codex_text = codex_doc.read_text()
    assert "keep-codex" in codex_text
    assert "agent-lb:coding-agent-routing" not in codex_text
    assert calls.read_text().splitlines() == [
        "mcp remove --scope user ccdex-worker",
        "mcp remove --scope user ccdex-worker",
    ]

    subprocess.run([str(INSTALLER), "--uninstall"], check=True, env=env, capture_output=True, text=True)
    assert not (bin_dir / "cc").exists()
    assert not (policy_dir / "coding-agents").exists()
    assert (bin_dir / "cc.pre-agent-lb").read_text() == "original wrapper\n"


def test_policy_installer_migrates_legacy_sections_and_preserves_unrelated_configuration(tmp_path: Path) -> None:
    home = tmp_path / "home"
    claude_doc = home / ".claude" / "CLAUDE.md"
    codex_doc = home / ".codex" / "AGENTS.md"
    settings_path = home / ".claude" / "settings.json"
    claude_doc.parent.mkdir(parents=True)
    codex_doc.parent.mkdir(parents=True)
    claude_doc.write_text(
        "# Global\n\nkeep-before\n\n"
        "## Orchestration — Fable architects, the fleet executes\n\n"
        "legacy sonnet routing\n\n## Project rules\n\nkeep-after\n"
    )
    codex_doc.write_text(f"# Codex\n\nkeep-codex\n\n{MARKER_BLOCK}\n## Safety\n\nkeep-safety\n")
    settings_path.write_text(
        """{
  "model": "opus",
  "env": {"KEEP": "yes"},
  "permissions": {"allow": ["keep-me"]},
  "hooks": {
    "PostToolUse": [{"matcher": "Skill", "hooks": [{"type": "command", "command": "keep-node-hook"}]}],
    "PreToolUse": [
      {"matcher": "Bash", "hooks": [
        {"type": "command", "command": "$HOME/.claude/hooks/ccdex-gpt-only.sh"},
        {"type": "command", "command": "keep-safety-hook"}
      ]},
      {"matcher": "Agent|Workflow", "hooks": [
        {"type": "command", "command": "$HOME/.claude/hooks/ccdex-gpt-only.sh"}
      ]}
    ]
  }
}
"""
    )

    subprocess.run([str(POLICY_INSTALLER), "--home", str(home)], check=True, capture_output=True, text=True)
    first = {path: path.read_bytes() for path in (claude_doc, codex_doc, settings_path)}
    subprocess.run([str(POLICY_INSTALLER), "--home", str(home)], check=True, capture_output=True, text=True)

    assert {path: path.read_bytes() for path in first} == first
    assert "keep-before" in claude_doc.read_text() and "keep-after" in claude_doc.read_text()
    assert "legacy sonnet routing" not in claude_doc.read_text()
    assert claude_doc.read_text().count("agent-lb:coding-agent-routing:start") == 1
    codex_text = codex_doc.read_text()
    assert "keep-codex" in codex_text and "keep-safety" in codex_text
    assert "agent-lb:coding-agent-routing" not in codex_text

    settings = json.loads(settings_path.read_text())
    assert settings["model"] == "claude-fable-5"
    assert settings["env"] == {"KEEP": "yes"}
    assert settings["permissions"] == {"allow": ["keep-me"]}
    assert settings["hooks"]["PostToolUse"][0]["hooks"][0]["command"] == "keep-node-hook"
    commands = [
        (group["matcher"], hook_config["command"])
        for group in settings["hooks"]["PreToolUse"]
        for hook_config in group["hooks"]
    ]
    assert commands == [("Bash", "keep-safety-hook")]


def test_policy_installer_preflight_failure_does_not_mutate_any_target(tmp_path: Path) -> None:
    home = tmp_path / "home"
    claude_doc = home / ".claude" / "CLAUDE.md"
    codex_doc = home / ".codex" / "AGENTS.md"
    settings_path = home / ".claude" / "settings.json"
    claude_doc.parent.mkdir(parents=True)
    codex_doc.parent.mkdir(parents=True)
    claude_doc.write_text("# Keep\n<!-- agent-lb:coding-agent-routing:start -->\nbroken\n")
    codex_doc.write_text("# Keep codex\n")
    settings_path.write_text('{"model": "keep"}\n')
    before = {path: path.read_bytes() for path in (claude_doc, codex_doc, settings_path)}

    result = subprocess.run(
        [str(POLICY_INSTALLER), "--home", str(home)],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "malformed or duplicate" in result.stderr
    assert {path: path.read_bytes() for path in before} == before


def test_policy_installer_invalid_json_does_not_mutate_documents(tmp_path: Path) -> None:
    home = tmp_path / "home"
    claude_doc = home / ".claude" / "CLAUDE.md"
    codex_doc = home / ".codex" / "AGENTS.md"
    settings_path = home / ".claude" / "settings.json"
    claude_doc.parent.mkdir(parents=True)
    codex_doc.parent.mkdir(parents=True)
    claude_doc.write_text("# Keep claude\n")
    codex_doc.write_text("# Keep codex\n")
    settings_path.write_text("{broken")
    before = {path: path.read_bytes() for path in (claude_doc, codex_doc, settings_path)}

    result = subprocess.run(
        [str(POLICY_INSTALLER), "--home", str(home)],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "invalid JSON" in result.stderr
    assert {path: path.read_bytes() for path in before} == before
