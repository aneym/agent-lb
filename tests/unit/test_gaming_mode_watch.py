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
    on = json.dumps({"version": 2, "holds": {"gaming-detector": {"bytes_per_sec": 1500000}}, "rate": 1500000})
    off = json.dumps({"version": 2, "holds": {}, "rate": 1500000})
    args_log = tmp_path / "throttle-args"
    throttle.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> {args_log}\n"
        f"if [ \"$2\" = on ]; then echo '{on}' > {state}; else echo '{off}' > {state}; fi\n"
    )
    throttle.chmod(0o755)
    return {
        **os.environ,
        "GAMING_MODE_HOME": str(home),
        "AGENT_LB_THROTTLE_FILE": str(state),
        "GAMING_MODE_SSH": str(ssh),
        "GAMING_MODE_THROTTLE_CMD": str(throttle),
        "GAMING_MODE_SIGNAL_FILE": str(tmp_path / "signal.json"),
        "GAMING_MODE_ACK_FILE": str(tmp_path / "ack" / "network.json"),
        "GAMING_MODE_GAMES_JSON": str(tmp_path / "games.json"),
        "GAMING_MODE_SIGNAL_MODE": "shadow",
    }


def _poll(env: dict[str, str]) -> None:
    subprocess.run([PYTHON, str(SCRIPT)], env=env, check=True, capture_output=True, text=True, timeout=30)


def _throttle(env: dict[str, str]) -> bool | None:
    path = Path(env["GAMING_MODE_HOME"]) / ".agent-lb" / "state" / "upload-throttle.json"
    return bool(json.loads(path.read_text())["holds"]) if path.exists() else None


def _set_tasklist(env: dict[str, str], tmp_path: Path, text: str) -> None:
    (tmp_path / "tasklist.txt").write_text(text)


CSV_GAME = (
    '"System.exe","4","Services","0","5,844 K"\n"VALORANT-Win64-Shipping.exe","11172","Console","1","1,197,300 K"\n'
)
CSV_IDLE = '"System.exe","4","Services","0","5,844 K"\n"explorer.exe","900","Console","1","90,000 K"\n'


def test_idle_first_deploy_does_not_release_an_unowned_default_throttle(tmp_path: Path) -> None:
    env = _setup(tmp_path, CSV_IDLE)
    _poll(env)
    assert _throttle(env) is None  # one empty poll: nothing changes yet
    _poll(env)
    assert _throttle(env) is None


def test_long_game_names_are_detected_and_turn_the_throttle_on(tmp_path: Path) -> None:
    env = _setup(tmp_path, CSV_IDLE)
    _poll(env)
    _poll(env)
    assert _throttle(env) is None
    _set_tasklist(env, tmp_path, CSV_GAME)
    _poll(env)
    assert _throttle(env) is True
    assert "--yields-to gaming-adaptive" in (tmp_path / "throttle-args").read_text()
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
    state.write_text('{"version": 2, "holds": {"gaming-detector": {"bytes_per_sec": 1500000}}, "rate": 1500000}')
    _poll(env)  # idle 1
    good_ssh = (tmp_path / "ssh").read_text()
    (tmp_path / "ssh").write_text("#!/bin/sh\nexit 255\n")
    _poll(env)  # failed poll
    (tmp_path / "ssh").write_text(good_ssh)
    _poll(env)  # idle 1 again, not 2
    assert _throttle(env) is True
    _poll(env)  # idle 2
    assert _throttle(env) is False


def test_stale_signal_falls_back_without_ack(tmp_path: Path) -> None:
    env = _setup(tmp_path, CSV_GAME)
    env["GAMING_MODE_SIGNAL_MODE"] = "enforce"
    Path(env["GAMING_MODE_SIGNAL_FILE"]).write_text(json.dumps({
        "generation": 7, "state": "idle", "mode": "normal",
        "read_at": "2000-01-01T00:00:00Z",
    }))
    _poll(env)
    assert _throttle(env) is True
    assert not Path(env["GAMING_MODE_ACK_FILE"]).exists()
    log = (Path(env["GAMING_MODE_HOME"]) / ".agent-lb" / "logs" / "gaming-mode.log").read_text()
    assert "falling back" in log


def test_generation_drop_is_accepted_and_logged_once(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    env = _setup(tmp_path, CSV_IDLE)
    env["GAMING_MODE_SIGNAL_MODE"] = "enforce"
    signal = Path(env["GAMING_MODE_SIGNAL_FILE"])
    for generation, state, mode in [(9, "active", "gaming"), (1, "idle", "normal"), (1, "idle", "normal")]:
        signal.write_text(json.dumps({
            "generation": generation, "state": state, "mode": mode,
            "read_at": datetime.now(timezone.utc).isoformat(),
        }))
        _poll(env)
    assert _throttle(env) is False
    ack = json.loads(Path(env["GAMING_MODE_ACK_FILE"]).read_text())
    assert (ack["generation"], ack["mode"], ack["ok"]) == (1, "normal", True)
    log = (Path(env["GAMING_MODE_HOME"]) / ".agent-lb" / "logs" / "gaming-mode.log").read_text()
    assert log.count("factory restart") == 1
