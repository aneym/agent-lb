from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from scripts.runtime_tree import Paths, log_line

PRIMARY_UNIT = "agent-lb"
FRONT_UNIT = "agent-lb-front"
FRONT_SCRIPT = Path("scripts/agent-lb-front.mjs")
STANDBY_UNIT = "agent-lb-standby"
STANDBY_PORT = 2459
HEALTHY_WAIT_S = 120
FRONT_WAIT_S = 10
STOP_WAIT_S = 450

Log = Callable[[str], None]


def front_digest(runtime: Path) -> str:
    try:
        return hashlib.sha256((runtime / FRONT_SCRIPT).read_bytes()).hexdigest()
    except OSError:
        return ""


def units_to_restart(units: list[str], front_before: str, front_after: str) -> list[str]:
    if front_before and front_before == front_after:
        return [unit for unit in units if unit.removesuffix(".service") != FRONT_UNIT]
    return units


def _systemctl(*args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=timeout)


def healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as resp:
            return resp.status == 200
    except (OSError, ValueError):
        return False


def _wait(ready: Callable[[], bool], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if ready():
            return True
        time.sleep(0.5)
    return ready()


def _front(front_state: Path) -> dict:
    try:
        if time.time() - front_state.stat().st_mtime > 5:
            return {}
        return json.loads(front_state.read_text())
    except (OSError, ValueError):
        return {}


def prefer(front_state: Path, port: int) -> bool:
    if not _wait(lambda: bool(_front(front_state).get("preferred_file")), FRONT_WAIT_S):
        return False
    state = _front(front_state)
    if port not in state.get("upstreams", []):
        return False
    target = Path(state["preferred_file"])
    temp = target.with_suffix(".tmp")
    temp.write_text(f"{port}\n")
    os.replace(temp, target)
    return _wait(lambda: _front(front_state).get("preferred") == port, FRONT_WAIT_S)


def _standby_command(primary: str, log_dir: Path) -> list[str]:
    shown = _systemctl(
        "show", primary, "-p", "ExecStart", "-p", "WorkingDirectory", "-p", "EnvironmentFiles", "-p", "Environment"
    ).stdout
    props: dict[str, str] = {}
    for key, value in (line.split("=", 1) for line in shown.splitlines() if "=" in line):
        props[key] = f"{props[key]}\n{value}" if key in props else value
    found = re.search(r"argv\[\]=(.*?) ;", props.get("ExecStart", ""))
    if found is None:
        raise RuntimeError(f"{primary} shows no ExecStart")
    args = found.group(1).split()
    if "--port" in args:
        args[args.index("--port") + 1] = str(STANDBY_PORT)
    else:
        args += ["--port", str(STANDBY_PORT)]
    log_file = log_dir / "agent-lb-standby.log"
    command = [
        "systemd-run",
        "--user",
        f"--unit={STANDBY_UNIT}",
        "--collect",
        "--quiet",
        f"--property=StandardOutput=append:{log_file}",
        f"--property=StandardError=append:{log_file}",
        "--property=TimeoutStopSec=420",
    ]
    if props.get("WorkingDirectory"):
        command.append(f"--working-directory={props['WorkingDirectory']}")
    command += [
        f"--property=EnvironmentFile={path}"
        for path in re.findall(r"(\S+) \(ignore_errors=", props.get("EnvironmentFiles", ""))
    ]
    command += [f"--setenv={entry}" for entry in shlex.split(props.get("Environment", ""))]
    return [*command, "/usr/bin/env", "AGENT_LB_METRICS_ENABLED=false", *args]


def start_standby(primary: str, log_dir: Path, log: Log) -> bool:
    if healthy(STANDBY_PORT):
        log(f"standby already serving on :{STANDBY_PORT}; using it")
        return True
    if _systemctl("is-active", STANDBY_UNIT).stdout.strip() in {"active", "activating", "deactivating"}:
        _systemctl("stop", STANDBY_UNIT, timeout=STOP_WAIT_S)
    _systemctl("reset-failed", STANDBY_UNIT)
    try:
        done = subprocess.run(_standby_command(primary, log_dir), capture_output=True, text=True, timeout=60)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        log(f"standby start failed: {exc}")
        return False
    if done.returncode:
        log(f"standby start failed: {(done.stderr or '').strip()[:200]}")
        return False
    if _wait(lambda: healthy(STANDBY_PORT), HEALTHY_WAIT_S):
        log(f"standby healthy on :{STANDBY_PORT}")
        return True
    log(f"standby not healthy on :{STANDBY_PORT} after {HEALTHY_WAIT_S}s; stopping it")
    _systemctl("stop", "--no-block", STANDBY_UNIT)
    return False


def restart(units: list[str], paths: Paths) -> tuple[int, list[str]]:
    health_url = os.environ.get("AGENT_LB_FOLLOW_HEALTH_URL") or "http://127.0.0.1:2477/health"
    port = urlsplit(health_url).port or 80
    primary = next((unit for unit in units if unit.removesuffix(".service") == PRIMARY_UNIT), None)
    front_state = paths.root.parent / "state" / "front.json"

    def log(line: str) -> None:
        log_line(paths, line)

    def terminate(signum: int, _frame: object) -> None:
        raise SystemExit(128 + signum)

    previous_handler = signal.signal(signal.SIGTERM, terminate)
    handoff = False
    try:
        if primary is not None:
            handoff = start_standby(primary, paths.root.parent, log)
            if handoff and not prefer(front_state, STANDBY_PORT):
                log(f"front would not prefer :{STANDBY_PORT}; restarting in place")
                _systemctl("stop", "--no-block", STANDBY_UNIT)
                handoff = False
            if handoff:
                log(f"front now prefers standby :{STANDBY_PORT}")
        try:
            done = _systemctl("restart", *units, timeout=600)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return 1, [str(exc)]
        if done.returncode:
            lines = (done.stderr or "").strip().splitlines()
            return done.returncode, [lines[-1] if lines else ""]
        if _wait(lambda: healthy(port), 90):
            return 0, [""]
        return 1, [f"{health_url} not healthy 90s after systemctl restart"]
    finally:
        if handoff and healthy(port):
            if prefer(front_state, port):
                _systemctl("stop", "--no-block", STANDBY_UNIT)
                log(f"front back on :{port}; standby on :{STANDBY_PORT} stopping")
            else:
                log(f"front did not move back to :{port}; standby keeps serving")
        elif handoff:
            log(f":{port} unhealthy; standby keeps serving on :{STANDBY_PORT}")
        signal.signal(signal.SIGTERM, previous_handler)
