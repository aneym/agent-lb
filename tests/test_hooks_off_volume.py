"""Integration: rendered hook settings survive an unavailable repository volume.

Regression: a sync restores repo-backed desktop/unblock hooks or the agent PATH.
Existing dispatcher parity does not exercise installation paths or a broken factory
symlink. This uses the installer CLI and real installed guard, without test seams.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = Path(os.environ.get("HOOK_SETTINGS_INSTALLER", ROOT / "config/coding-agents/install-policy.py"))
FACTORY = Path(os.environ.get("FACTORY_HOOK_SOURCE", Path.home() / ".agent-rails/factory-runtime"))


def test_rendered_hooks_survive_missing_factory(tmp_path):
    home = tmp_path / "home"
    claude = home / ".claude"
    claude.mkdir(parents=True)
    (home / "factory").symlink_to(tmp_path / "unavailable-volume")
    runtime = home / ".agent-rails/factory-runtime/bin"
    runtime.mkdir(parents=True)
    shutil.copy2(FACTORY / "bin/desktop-guard", runtime / "desktop-guard")
    settings_path = claude / "settings.json"
    settings_path.write_text(json.dumps({"hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command":
            '/usr/bin/python3 "$HOME/factory/bin/desktop-guard"'}]},
            {"matcher": "AskUserQuestion", "hooks": [{"type": "command", "command":
            '/opt/homebrew/bin/node "/Volumes/StudioExt/repos/personal/unblock/hooks/claude-ask.js"'}]}],
        "PermissionRequest": [{"hooks": [{"type": "command", "command":
            '/opt/homebrew/bin/node "/Volumes/StudioExt/repos/personal/unblock/hooks/claude-permission.js"'}]}],
        "PostToolUse": [{"hooks": [{"type": "command", "command":
            '"/opt/homebrew/bin/node" "/Users/aneyman/repos/skill-stats/dist/cli.js" hook'}]}],
        "SessionStart": [{"hooks": [{"type": "command", "command":
            'export PATH="$HOME/factory/bin/agent-shims:$PATH" # desktop-guard agent PATH'}]}]
    }}))
    env = dict(os.environ, HOME=str(home), HERDR_ENV="", HERDR_PANE_ID="")
    result = subprocess.run([sys.executable, str(INSTALLER), "--home", str(home),
                             "--hook-dispatcher", "off"], env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    settings = json.loads(settings_path.read_text())
    commands = [hook["command"] for groups in settings["hooks"].values()
                for group in groups for hook in group["hooks"] if hook.get("type") == "command"]
    for command in commands:
        assert "$HOME/factory/" not in command, command
        for word in re.findall(r'(?:\$HOME|/Users/aneyman|/Volumes)/[^\s\"\';&:]+', command):
            path = Path(word.replace("$HOME", str(home)))
            assert not str(path.resolve()).startswith("/Volumes/"), command
    desktop = next(command for command in commands if "/desktop-guard\"" in command)
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "printf hello"}})
    ran = subprocess.run(["/bin/sh", "-c", desktop], input=payload, env=env,
                         capture_output=True, text=True, timeout=10)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "missing" not in ran.stderr
    (runtime / "desktop-guard").unlink()
    missing = subprocess.run(["/bin/sh", "-c", desktop], input=payload, env=env,
                             capture_output=True, text=True, timeout=10)
    assert missing.returncode == 0
    assert len(missing.stderr.splitlines()) == 1
    assert "hook warning: missing" in missing.stderr
    for command in commands:
        if "/unblock-headless/" in command:
            missing = subprocess.run(["/bin/sh", "-c", command], input="{}", env=env,
                                     capture_output=True, text=True, timeout=10)
            assert missing.returncode == 0
            assert len(missing.stderr.splitlines()) == 1
    first = settings_path.read_text()
    rerun = subprocess.run([sys.executable, str(INSTALLER), "--home", str(home),
                            "--hook-dispatcher", "off"], env=env, capture_output=True, text=True, timeout=120)
    assert rerun.returncode == 0, rerun.stdout + rerun.stderr
    assert json.loads(first) == json.loads(settings_path.read_text())


def test_factory_installer_uses_internal_runtime(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path))
    result = subprocess.run([sys.executable, str(FACTORY / "bin/desktop-guard-install")],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    rendered = json.loads(result.stdout[result.stdout.index("{"):])
    assert '$HOME/.agent-rails/factory-runtime/bin/desktop-guard' in rendered["PreToolUse"]["hooks"][0]["command"]
    assert '$HOME/.agent-rails/factory-runtime/bin/agent-shims' in rendered["SessionStart"]["hooks"][0]["command"]
