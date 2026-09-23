from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "clients" / "gaming-mode-watch"
PYTHON = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else "python3"


def _setup(tmp_path: Path, tasklist: str | None) -> dict[str, str]:
    home = tmp_path / "home"
    (home / ".agent-lb" / "state").mkdir(parents=True)
    ssh = tmp_path / "ssh"
    if tasklist is None:
        ssh.write_text("#!/bin/sh\nexit 255\n")
    else:
        (tmp_path / "tasklist.txt").write_text(tasklist)
        warning = "** WARNING: connection is not using a post-quantum key exchange algorithm."
        ssh.write_text(f"#!/bin/sh\necho '{warning}'\ncat {tmp_path / 'tasklist.txt'}\n")
    ssh.chmod(0o755)
    throttle = tmp_path / "throttle"
    state = home / ".agent-lb" / "state" / "upload-throttle.json"
    throttle.write_text(
        "#!/bin/sh\n"
        f'if [ "$2" = on ]; then echo \'{{"enabled": true}}\' > {state}; '
        f"else echo '{{\"enabled\": false}}' > {state}; fi\n"
    )
    throttle.chmod(0o755)
    return {
        **os.environ,
        "GAMING_MODE_HOME": str(home),
        "GAMING_MODE_SSH": str(ssh),
        "GAMING_MODE_THROTTLE_CMD": str(throttle),
    }


def _poll(env: dict[str, str]) -> None:
    subprocess.run([PYTHON, str(SCRIPT)], env=env, check=True, capture_output=True, text=True, timeout=30)


def _throttle(env: dict[str, str]) -> bool | None:
    path = Path(env["GAMING_MODE_HOME"]) / ".agent-lb" / "state" / "upload-throttle.json"
    return json.loads(path.read_text())["enabled"] if path.exists() else None


def _set_tasklist(env: dict[str, str], tmp_path: Path, text: str) -> None:
    (tmp_path / "tasklist.txt").write_text(text)


CSV_GAME = '"System","4","Services","0","5,844 K"\n"VALORANT-Win64-Shipping.exe","11172","Console","1","1,197,300 K"\n'
CSV_IDLE = '"System","4","Services","0","5,844 K"\n"explorer.exe","900","Console","1","90,000 K"\n'


def test_idle_first_deploy_releases_the_default_throttle_after_two_polls(tmp_path: Path) -> None:
    env = _setup(tmp_path, CSV_IDLE)
    _poll(env)
    assert _throttle(env) is None  # one empty poll: nothing changes yet
    _poll(env)
    assert _throttle(env) is False
    assert (
        "OFF no game for 2 consecutive polls"
        in (Path(env["GAMING_MODE_HOME"]) / ".agent-lb" / "logs" / "gaming-mode.log").read_text()
    )


def test_long_game_names_are_detected_and_turn_the_throttle_on(tmp_path: Path) -> None:
    env = _setup(tmp_path, CSV_IDLE)
    _poll(env)
    _poll(env)
    assert _throttle(env) is False
    _set_tasklist(env, tmp_path, CSV_GAME)
    _poll(env)
    assert _throttle(env) is True
    log = (Path(env["GAMING_MODE_HOME"]) / ".agent-lb" / "logs" / "gaming-mode.log").read_text()
    assert "ON  game running: VALORANT-Win64-Shipping.exe" in log


def test_ssh_failure_keeps_the_last_state(tmp_path: Path) -> None:
    env = _setup(tmp_path, CSV_GAME)
    state = Path(env["GAMING_MODE_HOME"]) / ".agent-lb" / "state" / "upload-throttle.json"
    state.write_text('{"enabled": false}')
    _poll(env)
    assert _throttle(env) is True
    (tmp_path / "ssh").write_text("#!/bin/sh\nexit 255\n")
    for _ in range(3):
        _poll(env)
    assert _throttle(env) is True


def test_a_failed_poll_restarts_the_idle_count(tmp_path: Path) -> None:
    env = _setup(tmp_path, CSV_IDLE)
    state = Path(env["GAMING_MODE_HOME"]) / ".agent-lb" / "state" / "upload-throttle.json"
    state.write_text('{"enabled": true}')
    _poll(env)  # idle 1
    good_ssh = (tmp_path / "ssh").read_text()
    (tmp_path / "ssh").write_text("#!/bin/sh\nexit 255\n")
    _poll(env)  # failed poll
    (tmp_path / "ssh").write_text(good_ssh)
    _poll(env)  # idle 1 again, not 2
    assert _throttle(env) is True
    _poll(env)  # idle 2
    assert _throttle(env) is False
