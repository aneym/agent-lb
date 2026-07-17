from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "scripts" / "install-codex-routing-guard.sh"
LABEL = "com.aneyman.agent-lb-codex-routing-guard"
ROUTED_PROVIDER = """base_url = "http://127.0.0.1:2455/backend-api/codex"
wire_api = "responses"
supports_websockets = true
requires_openai_auth = true
"""

LAUNCHCTL_SHIM = r"""#!/usr/bin/env bash
set -euo pipefail
echo "launchctl $*" >> "$SHIM_CALL_LOG"
case "$1" in
  print) [[ -f "$SHIM_STATE/loaded" ]] ;;
  bootstrap) touch "$SHIM_STATE/loaded" ;;
  kickstart) touch "$SHIM_STATE/loaded" ;;
  bootout) rm -f "$SHIM_STATE/loaded" ;;
esac
"""


def _environment(tmp_path: Path, config: str) -> tuple[Path, dict[str, str], Path]:
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    state = tmp_path / "state"
    home.joinpath(".codex").mkdir(parents=True)
    home.joinpath(".codex", "config.toml").write_text(config)
    bin_dir.mkdir()
    state.mkdir()
    launchctl = bin_dir / "launchctl"
    launchctl.write_text(LAUNCHCTL_SHIM)
    launchctl.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CODEX_ROUTING_GUARD_HOME": str(home),
        "CODEX_ROUTING_GUARD_PYTHON": sys.executable,
        "CODEX_ROUTING_GUARD_SKIP_HEALTH": "1",
        "CODEX_ROUTING_GUARD_LOG": str(tmp_path / "guard.log"),
        "SHIM_STATE": str(state),
        "SHIM_CALL_LOG": str(tmp_path / "calls.log"),
    }
    return home, env, state


def _run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(INSTALLER), *args], cwd=ROOT, env=env, capture_output=True, text=True)


def test_preview_is_non_mutating_and_emits_launchagent(tmp_path: Path) -> None:
    home, env, _ = _environment(
        tmp_path,
        'model_provider = "agent-lb"\n\n[model_providers.agent-lb]\n' + ROUTED_PROVIDER,
    )

    result = _run(env, "--print")

    assert result.returncode == 0, result.stderr
    assert "Preview only" in result.stderr
    assert "Provider: agent-lb" in result.stderr
    plist = plistlib.loads(result.stdout.encode())
    config_path = home / ".codex" / "config.toml"
    assert plist["Label"] == LABEL
    assert plist["RunAtLoad"] is True
    assert plist["WatchPaths"] == [str(config_path)]
    assert plist["StartInterval"] == 300
    assert "KeepAlive" not in plist
    assert plist["ProgramArguments"] == [
        sys.executable,
        str(ROOT / "clients" / "codex-routing-guard"),
        "--config",
        str(config_path),
        "--provider",
        "agent-lb",
    ]
    assert not home.joinpath("Library").exists()


def test_install_selects_existing_codex_lb_repairs_and_loads_idempotently(tmp_path: Path) -> None:
    home, env, state = _environment(
        tmp_path,
        'model_provider = "openai"\n\n[model_providers.codex-lb]\n' + ROUTED_PROVIDER,
    )

    first = _run(env)
    assert first.returncode == 0, first.stderr
    config = home.joinpath(".codex", "config.toml").read_text()
    assert 'model_provider = "codex-lb"' in config
    plist_path = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    first_plist = plist_path.read_bytes()
    assert plistlib.loads(first_plist)["ProgramArguments"][-1] == "codex-lb"
    assert state.joinpath("loaded").exists()

    second = _run(env)

    assert second.returncode == 0, second.stderr
    assert plist_path.read_bytes() == first_plist
    assert config == home.joinpath(".codex", "config.toml").read_text()


def test_install_follows_config_symlink_and_watches_real_target(tmp_path: Path) -> None:
    home, env, _ = _environment(
        tmp_path,
        'model_provider = "openai"\n\n[model_providers.agent-lb]\n' + ROUTED_PROVIDER,
    )
    config_link = home / ".codex" / "config.toml"
    target = home / ".codex-shared" / "config.toml"
    target.parent.mkdir()
    config_link.replace(target)
    config_link.symlink_to(target)

    result = _run(env)

    assert result.returncode == 0, result.stderr
    assert config_link.is_symlink()
    assert 'model_provider = "agent-lb"' in target.read_text()
    plist_path = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    plist = plistlib.loads(plist_path.read_bytes())
    assert plist["WatchPaths"] == [str(target)]
    assert plist["ProgramArguments"][3] == str(target)


def test_explicit_provider_override_and_uninstall_remove_only_owned_plist(tmp_path: Path) -> None:
    home, env, state = _environment(tmp_path, 'model_provider = "openai"\n')
    env["CODEX_ROUTING_GUARD_PROVIDER"] = "codex-lb"

    install = _run(env)
    assert install.returncode == 0, install.stderr
    assert 'model_provider = "codex-lb"' in home.joinpath(".codex", "config.toml").read_text()

    uninstall = _run(env, "--uninstall")

    assert uninstall.returncode == 0, uninstall.stderr
    assert not home.joinpath("Library", "LaunchAgents", f"{LABEL}.plist").exists()
    assert not state.joinpath("loaded").exists()
    assert 'model_provider = "codex-lb"' in home.joinpath(".codex", "config.toml").read_text()
