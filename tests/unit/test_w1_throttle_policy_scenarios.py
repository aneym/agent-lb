"""W1 scenarios (incident 2026-09-30): owned throttle holds, unknown keeps protection, last-known-good.

These drive the real `agent-lb throttle` CLI and the real gaming-mode-watch script; only
ssh (the PC's tasklist) is faked.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
WATCH = ROOT / "clients" / "gaming-mode-watch"
WATCH_PYTHON = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else sys.executable

GAME = '"System","4","Services","0","5,844 K"\n"League of Legends.exe","11172","Console","1","1,197,300 K"\n'
IDLE = '"System","4","Services","0","5,844 K"\n"explorer.exe","900","Console","1","90,000 K"\n'
# A tasklist that did not come back whole: the second row is cut off mid-field.
TRUNCATED = '"System","4","Services","0","5,844 K"\n"League of Leg'


def _env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    state_dir = home / ".agent-lb" / "state"
    state_dir.mkdir(parents=True)
    state = state_dir / "upload-throttle.json"
    ssh = tmp_path / "ssh"
    ssh.write_text(f"#!/bin/sh\ncat {tmp_path / 'tasklist.txt'}\n")
    ssh.chmod(0o755)
    cli = tmp_path / "agent-lb"
    cli.write_text(
        "#!/bin/sh\n"
        f'cd "{ROOT}" && exec "{sys.executable}" -c '
        "'import sys; from app.cli import main; main(sys.argv[1:])' \"$@\"\n"
    )
    cli.chmod(0o755)
    return {
        **os.environ,
        "GAMING_MODE_HOME": str(home),
        "GAMING_MODE_SSH": str(ssh),
        "GAMING_MODE_THROTTLE_CMD": str(cli),
        "AGENT_LB_THROTTLE_FILE": str(state),
    }


def _tasklist(tmp_path: Path, text: str) -> None:
    (tmp_path / "tasklist.txt").write_text(text)


def _cli(env: dict[str, str], *args: str) -> str:
    result = subprocess.run(
        [env["GAMING_MODE_THROTTLE_CMD"], "throttle", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _poll(env: dict[str, str]) -> None:
    result = subprocess.run([WATCH_PYTHON, str(WATCH)], env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr


def _effective(env: dict[str, str]) -> tuple[bool, float]:
    code = "from app.core import upload_throttle as t; import json; print(json.dumps(t.read_state()))"
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60, check=True
    ).stdout
    enabled, rate = json.loads(out)
    return bool(enabled), float(rate)


def test_manual_hold_survives_the_detector_releasing(tmp_path: Path) -> None:
    env = _env(tmp_path)
    _cli(env, "on", "--owner", "incident-2026-09-30", "--rate-mbps", "0.2")
    _tasklist(tmp_path, GAME)
    _poll(env)
    _tasklist(tmp_path, IDLE)
    for _ in range(3):
        _poll(env)
    # The detector saw the game end and let go of its own hold; the incident hold stays.
    assert _effective(env) == (True, 200_000.0)
    status = _cli(env, "status")
    assert "incident-2026-09-30" in status
    _cli(env, "off", "--owner", "incident-2026-09-30")
    assert _effective(env)[0] is False


def test_the_strictest_hold_wins(tmp_path: Path) -> None:
    env = _env(tmp_path)
    _cli(env, "on", "--owner", "manual", "--rate-mbps", "1.0")
    _cli(env, "on", "--owner", "incident-2026-09-30", "--rate-mbps", "0.3")
    assert _effective(env) == (True, 300_000.0)
    _cli(env, "off", "--owner", "incident-2026-09-30")
    assert _effective(env) == (True, 1_000_000.0)


def test_a_tasklist_that_does_not_parse_keeps_protection(tmp_path: Path) -> None:
    env = _env(tmp_path)
    _tasklist(tmp_path, GAME)
    _poll(env)
    assert _effective(env)[0] is True
    _tasklist(tmp_path, TRUNCATED)
    for _ in range(3):
        _poll(env)
    assert _effective(env)[0] is True


def test_a_corrupt_state_file_keeps_the_last_good_rate(tmp_path: Path) -> None:
    env = _env(tmp_path)
    _cli(env, "on", "--owner", "manual", "--rate-mbps", "0.2")
    Path(env["AGENT_LB_THROTTLE_FILE"]).write_text("{not json")
    assert _effective(env) == (True, 200_000.0)
