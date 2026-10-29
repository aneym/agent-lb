#!/usr/bin/env python3

from __future__ import annotations

import os
import plistlib
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

LABEL = "com.aneyman.coding-agents-sync"


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


def write_plist(plist, data):
    plist.parent.mkdir(parents=True, exist_ok=True)
    if plist.exists():
        old = plist.read_bytes()
        if old == data:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        plist.with_name(f"{plist.name}.bak-{stamp}").write_bytes(old)
    plist.write_bytes(data)


def bootstrap(plist):
    domain = f"gui/{os.getuid()}"
    if subprocess.run(["launchctl", "print", f"{domain}/{LABEL}"], capture_output=True).returncode == 0:
        subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], check=True, capture_output=True)
    for pause in (0.25, 0.5, 1, 2, 4, None):
        result = subprocess.run(["launchctl", "bootstrap", domain, str(plist)], capture_output=True, text=True)
        if result.returncode == 0:
            return
        if result.returncode != 5 or pause is None:
            raise SystemExit(f"launchctl bootstrap {LABEL} failed (exit {result.returncode}): {result.stderr.strip()}")
        time.sleep(pause)


def main():
    home = Path.home()
    root = Path(__file__).resolve().parent.parent
    source = root / "scripts/coding_agents_sync/coding-agents-sync"
    payload = render(plistlib.loads((root / "deploy/launchd" / (LABEL + ".plist")).read_bytes()), home)
    if not Path(payload["ProgramArguments"][0]).is_file():
        raise SystemExit(f"coding-agents-sync interpreter missing: {payload['ProgramArguments'][0]}")
    (home / ".agent-lb/logs").mkdir(parents=True, exist_ok=True)
    link(home / ".local/bin/coding-agents-sync", source)
    plist = home / "Library/LaunchAgents" / (LABEL + ".plist")
    write_plist(plist, plistlib.dumps(payload))
    bootstrap(plist)
    print(f"{LABEL} runs {source}")


if __name__ == "__main__":
    main()
