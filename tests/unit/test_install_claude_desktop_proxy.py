from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts" / "install-claude-desktop-proxy.sh"
LABEL = "com.aneyman.agent-lb-claude-desktop-proxy"
PORT = 2458


LAUNCHCTL_SHIM = r"""#!/usr/bin/env bash
set -euo pipefail
echo "launchctl $*" >> "$SHIM_CALL_LOG"
case "$1" in
  print)
    [[ -f "$SHIM_STATE/loaded" ]]
    ;;
  bootstrap)
    touch "$SHIM_STATE/loaded" "$SHIM_STATE/listener"
    mkdir -p "$CLAUDE_DESKTOP_PROXY_HOME/.agent-lb/tls"
    touch "$CLAUDE_DESKTOP_PROXY_HOME/.agent-lb/tls/ca.pem"
    ;;
  kickstart)
    touch "$SHIM_STATE/loaded" "$SHIM_STATE/listener"
    ;;
  bootout)
    rm -f "$SHIM_STATE/loaded" "$SHIM_STATE/listener"
    ;;
esac
"""

LSOF_SHIM = r"""#!/usr/bin/env bash
set -euo pipefail
if [[ -f "$SHIM_STATE/listener" ]]; then
  echo "python 1234 user 12u IPv4 TCP 127.0.0.1:${CLAUDE_DESKTOP_PROXY_PORT:-2458} (LISTEN)"
fi
"""

CURL_SHIM = r"""#!/usr/bin/env bash
set -euo pipefail
if grep -q '"HTTPS_PROXY"' "$CLAUDE_DESKTOP_PROXY_HOME/.claude/settings.json" 2>/dev/null; then
  echo "health-after-settings" >> "$SHIM_CALL_LOG"
else
  echo "health-before-settings" >> "$SHIM_CALL_LOG"
fi
if [[ -f "$SHIM_STATE/fail-health" ]]; then
  exit 22
fi
echo '{"status":"ok"}'
"""


def _plist_path(home: Path) -> Path:
    return home / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _settings_path(home: Path) -> Path:
    return home / ".claude" / "settings.json"


def _backup_path(home: Path) -> Path:
    return home / ".agent-lb" / "claude-desktop-proxy-settings-backup.json"


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def _test_environment(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    state = tmp_path / "state"
    call_log = tmp_path / "calls.log"
    home.mkdir()
    bin_dir.mkdir()
    state.mkdir()
    for name, body in (
        ("launchctl", LAUNCHCTL_SHIM),
        ("lsof", LSOF_SHIM),
        ("curl", CURL_SHIM),
    ):
        shim = bin_dir / name
        shim.write_text(body)
        shim.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CLAUDE_DESKTOP_PROXY_HOME": str(home),
        "CLAUDE_DESKTOP_PROXY_PYTHON": sys.executable,
        "CLAUDE_DESKTOP_PROXY_READY_ATTEMPTS": "2",
        "CLAUDE_DESKTOP_PROXY_PORT_FREE_ATTEMPTS": "2",
        "SHIM_STATE": str(state),
        "SHIM_CALL_LOG": str(call_log),
    }
    return home, env, state


def _run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(INSTALLER), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def test_print_is_non_mutating_and_describes_dedicated_launch_agent(tmp_path: Path) -> None:
    home = tmp_path / "missing-home"
    env = {
        **os.environ,
        "CLAUDE_DESKTOP_PROXY_HOME": str(home),
        "CLAUDE_DESKTOP_PROXY_PYTHON": sys.executable,
    }

    result = _run(env, "--print")

    assert result.returncode == 0, result.stderr
    assert not home.exists()
    assert "Preview only; no files or processes will be changed." in result.stderr
    assert f"LaunchAgent: {LABEL}" in result.stderr
    assert "Proxy: http://127.0.0.1:2458 -> http://127.0.0.1:2455" in result.stderr
    assert f"Claude settings: {home / '.claude' / 'settings.json'}" in result.stderr
    assert "Rollback: --uninstall restores installer-owned settings" in result.stderr
    plist = plistlib.loads(result.stdout.encode())
    assert plist["Label"] == LABEL
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
    assert plist["ProgramArguments"] == [
        sys.executable,
        str(ROOT / "clients" / "claude-lb-launch"),
        "--desktop-proxy",
        "2458",
        "http://127.0.0.1:2455",
    ]
    assert plist["StandardOutPath"] == str(
        home / ".agent-lb" / "claude-desktop-proxy.out.log"
    )
    assert plist["StandardErrorPath"] == str(
        home / ".agent-lb" / "claude-desktop-proxy.err.log"
    )


def test_print_honors_port_upstream_and_python_overrides(tmp_path: Path) -> None:
    home = tmp_path / "missing-home"
    env = {
        **os.environ,
        "CLAUDE_DESKTOP_PROXY_HOME": str(home),
        "CLAUDE_DESKTOP_PROXY_PORT": "3458",
        "CLAUDE_DESKTOP_PROXY_UPSTREAM": "http://127.0.0.1:3455",
        "CLAUDE_DESKTOP_PROXY_PYTHON": sys.executable,
    }

    result = _run(env, "--print")

    assert result.returncode == 0, result.stderr
    plist = plistlib.loads(result.stdout.encode())
    assert plist["ProgramArguments"] == [
        sys.executable,
        str(ROOT / "clients" / "claude-lb-launch"),
        "--desktop-proxy",
        "3458",
        "http://127.0.0.1:3455",
    ]
    assert not home.exists()


def test_install_preserves_settings_and_cuts_over_only_after_proxy_health(tmp_path: Path) -> None:
    home, env, _ = _test_environment(tmp_path)
    settings_path = _settings_path(home)
    _write_json(
        settings_path,
        {
            "model": "claude-fable-5",
            "env": {"KEEP": "yes"},
            "permissions": {"allow": ["keep-me"]},
        },
    )

    result = _run(env)

    assert result.returncode == 0, result.stderr
    plist = plistlib.loads(_plist_path(home).read_bytes())
    assert plist["Label"] == LABEL
    assert plist["ProgramArguments"][1:] == [
        str(ROOT / "clients" / "claude-lb-launch"),
        "--desktop-proxy",
        "2458",
        "http://127.0.0.1:2455",
    ]
    settings = json.loads(settings_path.read_text())
    assert settings["model"] == "claude-fable-5"
    assert settings["permissions"] == {"allow": ["keep-me"]}
    assert settings["env"] == {
        "KEEP": "yes",
        "HTTPS_PROXY": "http://127.0.0.1:2458",
        "https_proxy": "http://127.0.0.1:2458",
        "NODE_EXTRA_CA_CERTS": str(home / ".agent-lb" / "tls" / "ca.pem"),
    }
    backup = json.loads(_backup_path(home).read_text())
    assert backup["previous"] == {
        "HTTPS_PROXY": {"present": False},
        "https_proxy": {"present": False},
        "NODE_EXTRA_CA_CERTS": {"present": False},
    }
    assert "health-before-settings" in Path(env["SHIM_CALL_LOG"]).read_text().splitlines()


def test_conflicting_preexisting_proxy_setting_is_refused_without_mutation(tmp_path: Path) -> None:
    home, env, _ = _test_environment(tmp_path)
    settings_path = _settings_path(home)
    original = {"env": {"HTTPS_PROXY": "http://127.0.0.1:9999", "KEEP": "yes"}}
    _write_json(settings_path, original)

    result = _run(env)

    assert result.returncode != 0
    assert "refusing to overwrite conflicting" in result.stderr
    assert json.loads(settings_path.read_text()) == original
    assert not _plist_path(home).exists()
    assert not _backup_path(home).exists()
    assert not Path(env["SHIM_CALL_LOG"]).exists()


def test_foreign_listener_is_refused_without_touching_settings_or_launchd(tmp_path: Path) -> None:
    home, env, state = _test_environment(tmp_path)
    state.joinpath("listener").touch()
    settings_path = _settings_path(home)
    _write_json(settings_path, {"env": {"KEEP": "yes"}})

    result = _run(env)

    assert result.returncode != 0
    assert "not owned" in result.stderr
    assert json.loads(settings_path.read_text()) == {"env": {"KEEP": "yes"}}
    assert not _plist_path(home).exists()
    calls = Path(env["SHIM_CALL_LOG"]).read_text().splitlines()
    assert all("bootstrap" not in call and "bootout" not in call for call in calls)


def test_failed_proxy_readiness_rolls_back_agent_before_settings_cutover(tmp_path: Path) -> None:
    home, env, state = _test_environment(tmp_path)
    state.joinpath("fail-health").touch()
    settings_path = _settings_path(home)
    original = {"env": {"KEEP": "yes"}, "hooks": {"keep": True}}
    _write_json(settings_path, original)

    result = _run(env)

    assert result.returncode != 0
    assert "settings were not changed" in result.stderr
    assert json.loads(settings_path.read_text()) == original
    assert not _backup_path(home).exists()
    assert not _plist_path(home).exists()
    assert not state.joinpath("loaded").exists()
    calls = Path(env["SHIM_CALL_LOG"]).read_text().splitlines()
    assert "health-before-settings" in calls
    assert any("bootout" in call for call in calls)


def test_install_is_idempotent(tmp_path: Path) -> None:
    home, env, _ = _test_environment(tmp_path)
    _write_json(_settings_path(home), {"env": {"KEEP": "yes"}})

    first = _run(env)
    assert first.returncode == 0, first.stderr
    paths = (_settings_path(home), _backup_path(home), _plist_path(home))
    first_bytes = {path: path.read_bytes() for path in paths}

    second = _run(env)

    assert second.returncode == 0, second.stderr
    assert {path: path.read_bytes() for path in paths} == first_bytes


def test_uninstall_conditionally_restores_owned_values_and_preserves_user_edits(
    tmp_path: Path,
) -> None:
    home, env, state = _test_environment(tmp_path)
    owned_proxy = "http://127.0.0.1:2458"
    settings_path = _settings_path(home)
    _write_json(settings_path, {"env": {"KEEP": "yes", "HTTPS_PROXY": owned_proxy}})
    install = _run(env)
    assert install.returncode == 0, install.stderr

    settings = json.loads(settings_path.read_text())
    settings["env"]["https_proxy"] = "http://user-edited.example:8080"
    settings["permissions"] = {"deny": ["keep-me"]}
    _write_json(settings_path, settings)

    uninstall = _run(env, "--uninstall")

    assert uninstall.returncode == 0, uninstall.stderr
    restored = json.loads(settings_path.read_text())
    assert restored == {
        "env": {
            "KEEP": "yes",
            "HTTPS_PROXY": owned_proxy,
            "https_proxy": "http://user-edited.example:8080",
        },
        "permissions": {"deny": ["keep-me"]},
    }
    assert not _backup_path(home).exists()
    assert not _plist_path(home).exists()
    assert not state.joinpath("loaded").exists()
