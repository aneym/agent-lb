#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path

CADDY = Path.home() / ".local/bin/caddy"


def systemctl(*args, check=True):
    return subprocess.run(["systemctl", "--user", *args], check=check, capture_output=True, text=True)


def active(unit):
    return systemctl("is-active", unit, check=False).stdout.strip() == "active"


def handoff(unit, config, env_file):
    if not active(unit):
        systemctl("restart", unit)
        print(f"{unit} was not active; started")
        return
    bridge = f"{unit.removesuffix('.service')}-handoff.service"
    if active(bridge):
        raise SystemExit(f"{bridge} is already serving; wait for it to drain")
    systemctl("reset-failed", bridge, check=False)
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    name = bridge.removesuffix(".service")
    started = subprocess.run(
        [
            "systemd-run",
            "--user",
            f"--unit={name}",
            "--property=Type=notify",
            f"--property=EnvironmentFile={env_file}",
            f"--property=RuntimeDirectory={name}",
            "--property=RuntimeDirectoryMode=0700",
            "--property=TimeoutStopSec=11min",
            f"--setenv=AGENT_LB_ADMIN_SOCKET={runtime}/{name}/admin.sock",
            str(CADDY),
            "run",
            "--config",
            str(config),
            "--adapter",
            "caddyfile",
        ],
        capture_output=True,
        text=True,
    )
    if started.returncode or not active(bridge):
        raise SystemExit(f"{bridge} did not start, {unit} left as it was: {started.stderr.strip()[-400:]}")
    print(f"{bridge} shares the listeners")
    began = time.monotonic()
    restarted = systemctl("restart", unit, check=False)
    if restarted.returncode or not active(unit):
        raise SystemExit(f"{unit} did not come back; {bridge} keeps serving: {restarted.stderr.strip()[-400:]}")
    print(f"{unit} restarted after the old process drained for {time.monotonic() - began:.1f} s")
    systemctl("stop", "--no-block", bridge)
    print(f"{bridge} stopping; it drains its own streams and exits")


def main():
    parser = argparse.ArgumentParser(
        description="restart a Caddy unit with no refused connections: a bridge Caddy shares its ports until the new process is up"
    )
    parser.add_argument("--unit", required=True)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--env-file", required=True, type=Path)
    args = parser.parse_args()
    handoff(args.unit, args.config, args.env_file)


if __name__ == "__main__":
    main()
