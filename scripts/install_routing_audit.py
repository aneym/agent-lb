#!/usr/bin/env python3
"""Install the read-only routing audit outside /Volumes for launchd."""
from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
from pathlib import Path

LABEL = "com.agentlb.routing-audit"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    home = Path.home()
    target = home / ".agent-lb/bin/routing_daily_audit.py"
    plist = home / "Library/LaunchAgents" / (LABEL + ".plist")
    domain = f"gui/{os.getuid()}"
    loaded = subprocess.run(["launchctl", "print", domain + "/" + LABEL], capture_output=True).returncode == 0
    if loaded:
        subprocess.run(["launchctl", "bootout", domain + "/" + LABEL], check=True, capture_output=True)
    if args.uninstall:
        plist.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        print("routing audit uninstalled")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    plist.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).with_name("routing_daily_audit.py"), target)
    target.chmod(0o755)
    # launchd calendars use local time. Wake at :05 each hour and guard in UTC so
    # the daily 11:05Z run stays correct across timezone and DST changes.
    payload = {
        "Label": LABEL,
        "ProgramArguments": ["/usr/bin/python3", str(target), "--scheduled", "--post"],
        "StartCalendarInterval": {"Minute": 5},
        "EnvironmentVariables": {"PATH": f"{home}/.local/bin:{home}/.agent-lb/bin:/opt/homebrew/bin:/usr/bin:/bin"},
        "StandardOutPath": str(home / ".agent-lb/routing-audit.out.log"),
        "StandardErrorPath": str(home / ".agent-lb/routing-audit.err.log"),
    }
    plist.write_bytes(plistlib.dumps(payload))
    subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=True, capture_output=True)
    print("routing audit installed for 11:05Z daily")


if __name__ == "__main__":
    main()
