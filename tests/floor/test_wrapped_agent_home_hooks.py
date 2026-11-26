from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "config/coding-agents"
EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Stop",
    "PreCompact",
    "SessionEnd",
)
CLAUDE_CONFIG_STUBS = (
    "agent-home-hooks.py",
    "compact-checkpoint.py",
    "factory-context.py",
    "focus-steal-check.py",
    "idle-agents.py",
    "main-checkout-write-warn.py",
    "subagent-closeout.py",
    "transcript_navigation.py",
)


def test_install_prunes_wrapped_agent_home_hooks_and_converges(tmp_path: Path, policy_checkout) -> None:
    source = tmp_path / "policy-repo" / "config/coding-agents"
    shutil.copytree(SOURCE, source)
    clients = source.parent.parent / "clients"
    clients.mkdir()
    for path in [ROOT / "clients/route", *sorted((ROOT / "clients").glob("route_*.py"))]:
        shutil.copy2(path, clients / path.name)
    policy_checkout(tmp_path / "policy-repo")

    home = tmp_path / "home"
    settings_path = home / ".claude/settings.json"
    settings_path.parent.mkdir(parents=True)
    floors = home / ".claude/rules/floors.md"
    floors.parent.mkdir(parents=True, exist_ok=True)
    floors.write_text("# Install fixture\n\n## Nested section\n\nFixture body.\n")
    hooks_dir = home / ".claude/hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    for name in CLAUDE_CONFIG_STUBS:
        (hooks_dir / name).write_text("import sys\nsys.exit(0)\n")

    seeded_hooks: dict[str, list[dict]] = {}
    for event in EVENTS:
        wrapped = (
            "if [ -f /etc/claude-code/managed-settings.json ]; then "
            f'exec /usr/bin/python3 "$HOME/.claude/hooks/agent-home-hooks.py" {event}; fi'
        )
        seeded_hooks[event] = [{"hooks": [{"type": "command", "command": wrapped, "timeout": 12}]}]
    settings_path.write_text(json.dumps({"hooks": seeded_hooks}, indent=2) + "\n")

    models_cache = tmp_path / "models-cache.json"
    models_cache.write_text(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "models": ["gpt-6.1-sol"]}))
    env = os.environ | {
        "HOME": str(home),
        "AGENT_LB_URL": "http://127.0.0.1:1",
        "ROUTE_MODELS_CACHE": str(models_cache),
        "PATH": os.pathsep.join((str(Path(sys.executable).parent), "/usr/bin", "/bin")),
    }
    for key in ("ROUTE_FIXTURE_DIR", "ROUTE_TABLE"):
        env.pop(key, None)

    def install() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(source / "install-policy.py"), "--home", str(home)],
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )

    first = install()
    for event in EVENTS:
        assert f"wrapped agent-home-hooks.py {event}" in first.stdout

    converged = json.loads(settings_path.read_text())
    for event in EVENTS:
        commands = [
            hook.get("command", "")
            for group in converged["hooks"].get(event, [])
            for hook in group.get("hooks", [])
            if isinstance(hook, dict)
        ]
        canonical = f'/usr/bin/python3 "$HOME/.claude/hooks/agent-home-hooks.py" {event}'
        assert all("/etc/claude-code/managed-settings.json" not in cmd for cmd in commands), event
        assert commands.count(canonical) == 1, (event, commands)

    second = install()
    assert "already converged" in second.stdout
