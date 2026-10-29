#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "scripts/coding_agents_sync"
SYNC = ("com.aneyman.coding-agents-sync", "coding-agents-sync")
FLEET = ("com.aneyman.coding-agents-fleet-status", "coding-agents-fleet-status")
UNITS = ("coding-agents-sync.service", "coding-agents-sync.timer")


def render(value, home):
    if isinstance(value, str):
        return value.replace("@HOME@", str(home))
    if isinstance(value, list):
        return [render(item, home) for item in value]
    if isinstance(value, dict):
        return {key: render(item, home) for key, item in value.items()}
    return value


def link(target, source):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() and os.readlink(target) == str(source):
        return
    temporary = target.with_name(f".{target.name}.install")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(source)
    os.replace(temporary, target)


def replace_backed_up(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        old = path.read_bytes()
        if old == data:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path.with_name(f"{path.name}.bak-{stamp}").write_bytes(old)
    path.write_bytes(data)


def bootstrap(label, plist):
    domain = f"gui/{os.getuid()}"
    if subprocess.run(["launchctl", "print", f"{domain}/{label}"], capture_output=True).returncode == 0:
        subprocess.run(["launchctl", "bootout", f"{domain}/{label}"], check=True, capture_output=True)
    for pause in (0.25, 0.5, 1, 2, 4, None):
        result = subprocess.run(["launchctl", "bootstrap", domain, str(plist)], capture_output=True, text=True)
        if result.returncode == 0:
            return
        if result.returncode != 5 or pause is None:
            raise SystemExit(f"launchctl bootstrap {label} failed (exit {result.returncode}): {result.stderr.strip()}")
        time.sleep(pause)


def install_launchd(home, jobs):
    (home / ".agent-lb/logs").mkdir(parents=True, exist_ok=True)
    for label, tool in jobs:
        payload = render(plistlib.loads((ROOT / "deploy/launchd" / (label + ".plist")).read_bytes()), home)
        if not Path(payload["ProgramArguments"][0]).is_file():
            raise SystemExit(f"{tool} interpreter missing: {payload['ProgramArguments'][0]}")
        link(home / ".local/bin" / tool, TOOLS / tool)
        plist = home / "Library/LaunchAgents" / (label + ".plist")
        replace_backed_up(plist, plistlib.dumps(payload))
        bootstrap(label, plist)
        print(f"{label} runs {TOOLS / tool}")


def install_systemd(home):
    link(home / ".local/bin" / SYNC[1], TOOLS / SYNC[1])
    units = home / ".config/systemd/user"
    for name in UNITS:
        replace_backed_up(units / name, (ROOT / "deploy/systemd" / name).read_bytes())
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", UNITS[1]], check=True)
    print(f"{UNITS[1]} runs {TOOLS / SYNC[1]}")


def main():
    parser = argparse.ArgumentParser(description="install coding-agents-sync from this checkout")
    parser.add_argument("--fleet-status", action="store_true", help="also install the fleet status job (macOS)")
    args = parser.parse_args()
    home = Path.home()
    if sys.platform == "darwin":
        install_launchd(home, [SYNC, FLEET] if args.fleet_status else [SYNC])
    elif args.fleet_status:
        raise SystemExit("the fleet status job runs on macOS only")
    else:
        install_systemd(home)


if __name__ == "__main__":
    main()
