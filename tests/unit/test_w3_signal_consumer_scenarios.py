"""W3 scenarios (incident 2026-09-30): gaming-mode-watch reads the factory's one gaming signal.

The factory's pc-gaming-mode writes one signal file (generation, state, mode). The watcher reads it
and writes a network ack for each generation. In shadow mode (the default) its own tasklist check
still decides and every disagreement is logged; in enforce mode the signal decides. These drive the
real watcher and the real `agent-lb throttle` CLI; only ssh (the PC's tasklist) is faked.
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

IDLE = '"System Idle Process","0","Services","0","8 K"\n"System","4","Services","0","5,844 K"\n'
LEAGUE = IDLE + '"League of Legends.exe","11172","Console","1","1,197,300 K"\n'
# A game only the factory's list knows about.
MARATHON = IDLE + '"Marathon.exe","2201","Console","1","900,000 K"\n'


def _env(tmp_path: Path, mode: str | None = None) -> dict[str, str]:
    home = tmp_path / "home"
    (home / ".agent-lb" / "state").mkdir(parents=True)
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
    games = tmp_path / "pc-games.json"
    games.write_text(json.dumps({"version": 1, "games": ["League of Legends", "Marathon"], "launchers": []}))
    env = {
        **os.environ,
        "GAMING_MODE_HOME": str(home),
        "GAMING_MODE_SSH": str(ssh),
        "GAMING_MODE_THROTTLE_CMD": str(cli),
        "AGENT_LB_THROTTLE_FILE": str(home / ".agent-lb" / "state" / "upload-throttle.json"),
        "GAMING_MODE_SIGNAL_FILE": str(tmp_path / "pc-game-signal.json"),
        "GAMING_MODE_ACK_FILE": str(tmp_path / "pc-game-ack" / "network.json"),
        "GAMING_MODE_GAMES_JSON": str(games),
    }
    env.pop("GAMING_MODE_SIGNAL_MODE", None)
    if mode:
        env["GAMING_MODE_SIGNAL_MODE"] = mode
    return env


def _tasklist(tmp_path: Path, text: str) -> None:
    (tmp_path / "tasklist.txt").write_text(text)


def _signal(env: dict[str, str], generation: int, state: str, mode: str) -> None:
    from datetime import datetime, timezone

    Path(env["GAMING_MODE_SIGNAL_FILE"]).write_text(
        json.dumps(
            {
                "version": 1,
                "generation": generation,
                "state": state,
                "mode": mode,
                "games": ["League of Legends"] if mode == "gaming" else [],
                "read_at": datetime.now(timezone.utc).isoformat(),
                "mode_since": datetime.now(timezone.utc).isoformat(),
                "idle_reads": 0,
                "override": None,
            }
        )
    )


def _poll(env: dict[str, str]) -> None:
    result = subprocess.run([WATCH_PYTHON, str(WATCH)], env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr


def _detector_holds(env: dict[str, str]) -> bool:
    code = "from app.core import upload_throttle as t; import json; print(json.dumps(sorted(t.read_policy()['holds'])))"
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60, check=True
    ).stdout
    return "gaming-detector" in json.loads(out)


def _ack(env: dict[str, str]) -> dict:
    return json.loads(Path(env["GAMING_MODE_ACK_FILE"]).read_text())


def test_shadow_keeps_its_own_decision_logs_the_disagreement_and_acks(tmp_path: Path) -> None:
    env = _env(tmp_path)  # shadow is the default
    _tasklist(tmp_path, IDLE)
    _signal(env, 7, "active", "gaming")
    _poll(env)
    # The tasklist shows no game, so the detector takes no hold, whatever the signal says.
    assert _detector_holds(env) is False
    ack = _ack(env)
    assert (ack["generation"], ack["mode"], ack["ok"]) == (7, "normal", True)
    shadow = (Path(env["GAMING_MODE_HOME"]) / ".agent-lb" / "state" / "gaming-signal-shadow.tsv").read_text()
    row = shadow.strip().splitlines()[-1].split("\t")
    assert row[1:5] == ["7", "active", "gaming", "normal"]


def test_enforce_follows_the_signal_and_unknown_keeps_protection(tmp_path: Path) -> None:
    env = _env(tmp_path, "enforce")
    _tasklist(tmp_path, IDLE)
    _signal(env, 3, "active", "gaming")
    _poll(env)
    assert _detector_holds(env) is True
    assert (_ack(env)["generation"], _ack(env)["mode"]) == (3, "gaming")
    _signal(env, 3, "unknown", "gaming")
    _poll(env)
    assert _detector_holds(env) is True
    _signal(env, 4, "idle", "normal")
    _poll(env)
    assert _detector_holds(env) is False
    assert (_ack(env)["generation"], _ack(env)["mode"], _ack(env)["ok"]) == (4, "normal", True)


def test_enforce_without_a_readable_signal_falls_back_to_the_tasklist(tmp_path: Path) -> None:
    env = _env(tmp_path, "enforce")
    Path(env["GAMING_MODE_SIGNAL_FILE"]).write_text("{not json")
    _tasklist(tmp_path, LEAGUE)
    _poll(env)
    assert _detector_holds(env) is True
    log = (Path(env["GAMING_MODE_HOME"]) / ".agent-lb" / "logs" / "gaming-mode.log").read_text()
    assert "falling back to tasklist" in log


def test_the_factory_game_list_is_used(tmp_path: Path) -> None:
    env = _env(tmp_path)
    _tasklist(tmp_path, MARATHON)
    _poll(env)
    assert _detector_holds(env) is True
