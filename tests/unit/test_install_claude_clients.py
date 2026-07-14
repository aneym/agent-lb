from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts" / "install-claude-clients.sh"
POLICY_INSTALLER = ROOT / "config" / "coding-agents" / "install-policy.py"


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
    assert f"link {user_home}/.claude/hooks/ccdex-gpt-only.sh" in result.stdout
    assert f"mcp ccdex-worker -> {bin_dir}/ccdex-worker-mcp (user scope)" in result.stdout


def test_installer_converges_links_registration_and_uninstall(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    policy_dir = tmp_path / "policy"
    user_home = tmp_path / "home"
    bin_dir.mkdir()
    original = bin_dir / "cc"
    original.write_text("original wrapper\n")
    calls = tmp_path / "claude-calls"
    claude = tmp_path / "claude"
    claude.write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "{calls}"\n')
    claude.chmod(0o755)
    hook = user_home / ".claude" / "hooks" / "ccdex-gpt-only.sh"
    hook.parent.mkdir(parents=True)
    hook.write_text("#!/bin/sh\necho user-hook\n")
    hook.chmod(0o755)
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
    for name in ("cc", "ccdex", "ccdex-worker-mcp"):
        assert (bin_dir / name).is_symlink()
        assert (bin_dir / name).resolve() == (ROOT / "clients" / name).resolve()
    assert (policy_dir / "coding-agents").is_symlink()
    assert (policy_dir / "coding-agents").resolve() == (ROOT / "config" / "coding-agents").resolve()
    assert hook.is_symlink()
    assert hook.resolve() == (ROOT / "config" / "coding-agents" / "ccdex-gpt-only.sh").resolve()
    assert (hook.parent / "ccdex-gpt-only.sh.pre-agent-lb").read_text() == "#!/bin/sh\necho user-hook\n"
    assert (user_home / ".claude" / "CLAUDE.md").read_text().count("agent-lb:coding-agent-routing:start") == 1
    assert (user_home / ".codex" / "AGENTS.md").read_text().count("agent-lb:coding-agent-routing:start") == 1
    assert calls.read_text().splitlines() == [
        "mcp remove --scope user ccdex-worker",
        f"mcp add --scope user ccdex-worker -- {bin_dir}/ccdex-worker-mcp",
        "mcp remove --scope user ccdex-worker",
        f"mcp add --scope user ccdex-worker -- {bin_dir}/ccdex-worker-mcp",
    ]

    subprocess.run([str(INSTALLER), "--uninstall"], check=True, env=env, capture_output=True, text=True)
    assert not any((bin_dir / name).exists() for name in ("cc", "ccdex", "ccdex-worker-mcp"))
    assert not (policy_dir / "coding-agents").exists()
    assert not hook.exists()
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
    codex_doc.write_text(
        "# Codex\n\nkeep-codex\n\n## Fable/Codex Routing\n\nlegacy opus routing\n\n## Safety\n\nkeep-safety\n"
    )
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
    assert "keep-codex" in codex_doc.read_text() and "keep-safety" in codex_doc.read_text()
    assert "legacy opus routing" not in codex_doc.read_text()
    for path in (claude_doc, codex_doc):
        assert path.read_text().count("agent-lb:coding-agent-routing:start") == 1
        assert path.read_text().count("agent-lb:coding-agent-routing:end") == 1

    settings = __import__("json").loads(settings_path.read_text())
    assert settings["model"] == "claude-fable-5"
    assert settings["env"] == {"KEEP": "yes"}
    assert settings["permissions"] == {"allow": ["keep-me"]}
    assert settings["hooks"]["PostToolUse"][0]["hooks"][0]["command"] == "keep-node-hook"
    commands = [
        (group["matcher"], hook_config["command"])
        for group in settings["hooks"]["PreToolUse"]
        for hook_config in group["hooks"]
    ]
    assert ("Bash", "keep-safety-hook") in commands
    assert commands.count(("Agent|Workflow", "$HOME/.claude/hooks/ccdex-gpt-only.sh")) == 1
    assert commands.count(("Bash", "$HOME/.claude/hooks/ccdex-gpt-only.sh")) == 1


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


def test_installer_hook_conflict_fails_before_policy_mutation(tmp_path: Path) -> None:
    home = tmp_path / "home"
    hook = home / ".claude" / "hooks" / "ccdex-gpt-only.sh"
    hook.parent.mkdir(parents=True)
    hook.write_text("user hook\n")
    (hook.parent / "ccdex-gpt-only.sh.pre-agent-lb").write_text("older backup\n")
    claude = tmp_path / "claude"
    claude.write_text("#!/usr/bin/env bash\nexit 0\n")
    claude.chmod(0o755)

    result = subprocess.run(
        [str(INSTALLER)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "AGENT_LB_USER_HOME": str(home),
            "AGENT_LB_CLIENT_BIN_DIR": str(tmp_path / "bin"),
            "AGENT_LB_POLICY_DIR": str(tmp_path / "policy"),
            "AGENT_LB_CLAUDE_BIN": str(claude),
        },
    )

    assert result.returncode != 0
    assert "backup already exists" in result.stderr
    assert not (home / ".claude" / "CLAUDE.md").exists()
    assert not (home / ".codex" / "AGENTS.md").exists()
    assert not (home / ".claude" / "settings.json").exists()
