#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

LABEL = "com.agentlb.routing-audit"
TAB_LABEL = "com.agentlb.tab-token-audit"


def install_tab_audit(home: Path, domain: str, uninstall: bool) -> None:
    plist = home / "Library/LaunchAgents" / (TAB_LABEL + ".plist")
    if subprocess.run(["launchctl", "print", domain + "/" + TAB_LABEL], capture_output=True).returncode == 0:
        subprocess.run(["launchctl", "bootout", domain + "/" + TAB_LABEL], check=True, capture_output=True)
    if uninstall:
        plist.unlink(missing_ok=True)
        return
    executable = shutil.which("agent-lb")
    if not executable:
        raise SystemExit("Install agent-lb on PATH before installing the tab token audit")
    template = Path(__file__).resolve().parent.parent / "deploy/launchd" / (TAB_LABEL + ".plist")
    payload = plistlib.loads(template.read_bytes())
    payload["ProgramArguments"] = [
        value.replace("@AGENT_LB@", executable).replace("@HOME@", str(home)) for value in payload["ProgramArguments"]
    ]
    for key in ("StandardOutPath", "StandardErrorPath", "WorkingDirectory"):
        payload[key] = payload[key].replace("@HOME@", str(home))
    payload["EnvironmentVariables"]["PATH"] = payload["EnvironmentVariables"]["PATH"].replace("@HOME@", str(home))
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_bytes(plistlib.dumps(payload))
    subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=True, capture_output=True)
    print("tab token audit installed for 07:20 local daily")


def main():
    parser = argparse.ArgumentParser(description="Install the read-only routing audit outside /Volumes for launchd.")
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
        install_tab_audit(home, domain, True)
        print("routing audit uninstalled")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    plist.parent.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parent
    shutil.copyfile(source / "routing_daily_audit.py", target)
    for module in source.glob("routing_audit_*.py"):
        shutil.copyfile(module, target.with_name(module.name))
    target.chmod(0o755)

    runtime_python = home / ".agent-lb/runtime/agent-lb/.venv/bin/python"
    interpreter = str(runtime_python) if runtime_python.is_file() else str(Path(sys.executable).resolve())
    payload = {
        "Label": LABEL,
        "ProgramArguments": [interpreter, str(target), "--scheduled", "--post"],
        "StartCalendarInterval": {"Minute": 5},
        "EnvironmentVariables": {"PATH": f"{home}/.local/bin:{home}/.agent-lb/bin:/opt/homebrew/bin:/usr/bin:/bin"},
        "StandardOutPath": str(home / ".agent-lb/routing-audit.out.log"),
        "StandardErrorPath": str(home / ".agent-lb/routing-audit.err.log"),
    }
    plist.write_bytes(plistlib.dumps(payload))
    subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=True, capture_output=True)
    install_tab_audit(home, domain, False)
    print("routing audit installed for 11:05Z daily")


if __name__ == "__main__":
    main()
