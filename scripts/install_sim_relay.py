#!/usr/bin/env python3

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import time
from pathlib import Path

LABEL = "com.agent-lb.sim-relay"


def render(value, home):
    if isinstance(value, str):
        return value.replace("@HOME@", str(home))
    if isinstance(value, list):
        return [render(item, home) for item in value]
    if isinstance(value, dict):
        return {key: render(item, home) for key, item in value.items()}
    return value


def main():
    home = Path.home()
    root = Path(__file__).resolve().parent.parent
    payload = render(plistlib.loads((root / "deploy/launchd" / (LABEL + ".plist")).read_bytes()), home)
    missing = [path for path in payload["ProgramArguments"][:3] if not Path(path).is_file()]
    if missing:
        raise SystemExit(f"sim-relay launcher missing: {', '.join(missing)}")
    target = home / ".local/bin/sim-relay"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / "scripts/sim-relay.py", target)
    target.chmod(0o755)
    plist = home / "Library/LaunchAgents" / (LABEL + ".plist")
    plist.write_bytes(plistlib.dumps(payload))
    domain = f"gui/{os.getuid()}"
    if subprocess.run(["launchctl", "print", f"{domain}/{LABEL}"], capture_output=True).returncode == 0:
        subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], check=True, capture_output=True)
    for pause in (0.25, 0.5, 1, 2, 4, None):
        result = subprocess.run(["launchctl", "bootstrap", domain, str(plist)], capture_output=True, text=True)
        if result.returncode == 0:
            break
        if result.returncode != 5 or pause is None:
            raise SystemExit(f"launchctl bootstrap {LABEL} failed (exit {result.returncode}): {result.stderr.strip()}")
        time.sleep(pause)
    print(f"{LABEL} installed from the pinned uv python behind the bun anchor")


if __name__ == "__main__":
    main()
