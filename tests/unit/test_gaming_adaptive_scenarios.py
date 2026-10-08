"""Gaming adaptive cap scenarios (2026-10-01): the upload cap follows the PC's real ping.

While Alex games, floor holds (p6-gaming, gaming-detector) keep uploads at 0.2 MB/s. They yield
to a fresh gaming-adaptive hold, which a Studio controller rewrites every 30 s from the PC HUD's
ping samples: 2 minutes of baseline at 0.2, then +0.1 MB/s every 2 minutes up to 1.0 while
internet and game ping p95 stay within baseline + 15 ms with no loss. Any breach drops it to 0.2
for 10 minutes. Stale samples or a dead controller hand the cap back to the floor holds.

These drive the real `agent-lb throttle` CLI and the real gaming-adaptive-cap script; only the
PC's samples (ssh) and the controller's clock are faked.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "clients" / "gaming-adaptive-cap"
CONTROLLER_PYTHON = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else sys.executable
T0 = datetime(2026, 10, 1, 15, 0, 0, tzinfo=timezone.utc)


def _iso(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    state_dir = home / ".agent-lb" / "state"
    state_dir.mkdir(parents=True)
    samples = tmp_path / "samples.csv"
    samples.write_text("")
    cli = tmp_path / "agent-lb"
    cli.write_text(
        "#!/bin/sh\n"
        f'cd "{ROOT}" && exec "{sys.executable}" -c '
        "'import sys; from app.cli import main; main(sys.argv[1:])' \"$@\"\n"
    )
    cli.chmod(0o755)
    return {
        **os.environ,
        "GAMING_ADAPTIVE_HOME": str(home),
        "GAMING_ADAPTIVE_SAMPLES_CMD": f"cat {samples}",
        "GAMING_ADAPTIVE_THROTTLE_CMD": str(cli),
        "GAMING_ADAPTIVE_MODE": "enforce",
        "AGENT_LB_THROTTLE_FILE": str(state_dir / "upload-throttle.json"),
    }


def _cli(env: dict[str, str], *args: str) -> str:
    result = subprocess.run(
        [env["GAMING_ADAPTIVE_THROTTLE_CMD"], "throttle", *args], env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _effective(env: dict[str, str]) -> tuple[bool, float]:
    code = "from app.core import upload_throttle as t; import json; print(json.dumps(t.read_state()))"
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60, check=True
    ).stdout
    enabled, rate = json.loads(out)
    return bool(enabled), float(rate)


def _holds(env: dict[str, str]) -> dict:
    return json.loads(Path(env["AGENT_LB_THROTTLE_FILE"]).read_text())["holds"]


def _samples(tmp_path: Path, now: datetime, inet: float = 20.0, game: float = 30.0, loss: float = 0.0) -> None:
    """The HUD's CSV tail: a row every 5 s over the last minute (no header, like Get-Content -Tail)."""
    rows = []
    for back in range(55, -5, -5):
        stamp = _iso(now - timedelta(seconds=back))
        rows.append(f"{stamp},2,5,0,4,9,0,{inet},{inet + 10},{loss},{game},{game + 10},{loss},300,40,")
    (tmp_path / "samples.csv").write_text("\n".join(rows) + "\n")


def _run(env: dict[str, str], now: datetime) -> None:
    result = subprocess.run(
        [CONTROLLER_PYTHON, str(CONTROLLER)],
        env={**env, "GAMING_ADAPTIVE_NOW": _iso(now)},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr


def _floor(env: dict[str, str]) -> None:
    _cli(
        env, "on", "--owner", "p6-gaming", "--rate-mbps", "0.2", "--yields-to", "gaming-adaptive", "--reason", "League"
    )


def test_a_fresh_adaptive_hold_lifts_the_floor_and_a_stale_one_hands_back(tmp_path: Path) -> None:
    env = _env(tmp_path)
    _floor(env)
    _cli(env, "on", "--owner", "gaming-adaptive", "--rate-mbps", "0.5", "--reason", "ping ok")
    # The floor yields to the fresh adaptive hold.
    assert _effective(env) == (True, 500_000.0)
    # A hold that does not yield still caps everything.
    _cli(env, "on", "--owner", "incident", "--rate-mbps", "0.3")
    assert _effective(env) == (True, 300_000.0)
    _cli(env, "off", "--owner", "incident")
    # The controller stopped rewriting its hold two minutes ago: the floor is back.
    path = Path(env["AGENT_LB_THROTTLE_FILE"])
    state = json.loads(path.read_text())
    state["holds"]["gaming-adaptive"]["since"] = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    path.write_text(json.dumps(state))
    assert _effective(env) == (True, 200_000.0)
    assert "yields to gaming-adaptive" in _cli(env, "status")


def test_the_cap_steps_up_while_ping_holds_and_drops_on_a_breach(tmp_path: Path) -> None:
    env = _env(tmp_path)
    _floor(env)
    for minute in (0, 1, 2):  # baseline: the first 2 minutes at 0.2
        now = T0 + timedelta(minutes=minute)
        _samples(tmp_path, now)
        _run(env, now)
    assert _effective(env) == (True, 200_000.0)
    for minute, expected in ((4, 300_000.0), (6, 400_000.0), (8, 500_000.0)):
        now = T0 + timedelta(minutes=minute)
        _samples(tmp_path, now, inet=24.0, game=33.0)  # a little worse, still within baseline + 15 ms
        _run(env, now)
        assert _effective(env) == (True, expected), f"minute {minute}"
    breach = T0 + timedelta(minutes=9)
    _samples(tmp_path, breach, inet=60.0)  # internet p95 40 ms over baseline
    _run(env, breach)
    assert _effective(env) == (True, 200_000.0)
    for minute in (11, 15, 18):  # held at 0.2 for 10 minutes even though ping recovered
        now = T0 + timedelta(minutes=minute)
        _samples(tmp_path, now)
        _run(env, now)
        assert _effective(env) == (True, 200_000.0), f"minute {minute}"
    loss = T0 + timedelta(minutes=19)
    _samples(tmp_path, loss, loss=3.0)
    _run(env, loss)
    log = (Path(env["GAMING_ADAPTIVE_HOME"]) / ".agent-lb" / "logs" / "gaming-adaptive.tsv").read_text()
    assert len(log.strip().splitlines()) >= 11  # one line per run (a header line is allowed)
    assert "breach" in log


def test_the_cap_stops_at_one_mb_per_second(tmp_path: Path) -> None:
    env = _env(tmp_path)
    _floor(env)
    for minute in range(0, 26, 2):
        now = T0 + timedelta(minutes=minute)
        _samples(tmp_path, now)
        _run(env, now)
    assert _effective(env) == (True, 1_000_000.0)


def test_shadow_mode_only_logs(tmp_path: Path) -> None:
    env = {**_env(tmp_path), "GAMING_ADAPTIVE_MODE": "shadow"}
    _floor(env)
    for minute in (0, 1, 2, 4, 6):
        now = T0 + timedelta(minutes=minute)
        _samples(tmp_path, now)
        _run(env, now)
    assert "gaming-adaptive" not in _holds(env)
    assert _effective(env) == (True, 200_000.0)
    log = (Path(env["GAMING_ADAPTIVE_HOME"]) / ".agent-lb" / "logs" / "gaming-adaptive.tsv").read_text()
    assert "shadow" in log and "0.40" in log  # what it would have set by minute 6


def test_stale_samples_and_the_end_of_the_game_hand_back(tmp_path: Path) -> None:
    env = _env(tmp_path)
    _floor(env)
    for minute in (0, 1, 2, 4):
        now = T0 + timedelta(minutes=minute)
        _samples(tmp_path, now)
        _run(env, now)
    assert _effective(env) == (True, 300_000.0)
    # The HUD stopped writing three minutes ago: the controller lets go of its hold.
    _samples(tmp_path, T0 + timedelta(minutes=2))
    _run(env, T0 + timedelta(minutes=5))
    assert "gaming-adaptive" not in _holds(env)
    assert _effective(env) == (True, 200_000.0)
    # Samples come back, the controller climbs again, then the game ends (no floor hold left).
    for minute in (6, 7, 8, 10):
        now = T0 + timedelta(minutes=minute)
        _samples(tmp_path, now)
        _run(env, now)
    assert _effective(env)[1] > 200_000.0
    _cli(env, "off", "--owner", "p6-gaming")
    _run(env, T0 + timedelta(minutes=11))
    assert _holds(env) == {}
    assert _effective(env)[0] is False


def test_the_default_samples_read_comes_from_pc_wsl_never_windows_ssh(tmp_path: Path) -> None:
    """A Windows-side `ssh pc` opens a console over the game (2026-10-08); PingHud samples come from pc-wsl's pc-facts."""
    env = _env(tmp_path)
    del env["GAMING_ADAPTIVE_SAMPLES_CMD"]
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "ssh").write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {tmp_path / 'ssh-args'}\nexit 255\n")
    (shim / "ssh").chmod(0o755)
    env["PATH"] = f"{shim}:{env['PATH']}"
    _floor(env)
    _run(env, datetime.now(timezone.utc))
    args = (tmp_path / "ssh-args").read_text().split("\n")
    assert "pc-wsl" in args and "pc" not in args
