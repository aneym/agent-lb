from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from policy_layout import POLICY_DIR  # type: ignore[missing-import]

NAME = "codex-rollout-scrub"
LABEL = f"com.aneym.{NAME}"
INTERVAL = 30
UNIT_DIR = Path(".config/systemd/user")
PLIST = Path("Library/LaunchAgents") / f"{LABEL}.plist"
STATE_LOG = Path(".local/state/codex-rollout-scrub/scrub.log")


def scrub_command(home: Path) -> list[str]:
    return [sys.executable, str(home / POLICY_DIR / "codex_rollout_scrub.py"), "--quiet"]


def systemd_units(home: Path) -> dict[str, str]:
    command = " ".join(scrub_command(home))
    return {
        f"{NAME}.service": (
            "[Unit]\nDescription=Mask claim codes in Codex rollouts\n\n"
            f"[Service]\nType=oneshot\nNice=10\nIOSchedulingClass=idle\nExecStart={command}\n"
        ),
        f"{NAME}.timer": (
            "[Unit]\nDescription=Mask claim codes in Codex rollouts\n\n"
            f"[Timer]\nOnBootSec=1min\nOnUnitActiveSec={INTERVAL}s\nAccuracySec=5s\n\n"
            "[Install]\nWantedBy=timers.target\n"
        ),
    }


def plist_text(home: Path) -> str:
    args = "".join(f"<string>{part}</string>" for part in scrub_command(home))
    log = home / STATE_LOG
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>{args}</array>
  <key>EnvironmentVariables</key>
  <dict><key>HOME</key><string>{home}</string></dict>
  <key>StartInterval</key><integer>{INTERVAL}</integer>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""


def run(*command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), capture_output=True, text=True, timeout=60)


def ensure_systemd(home: Path, install: bool) -> str:
    folder = home / UNIT_DIR
    units = systemd_units(home)
    changed = False
    for name, text in units.items():
        path = folder / name
        if install and (not path.is_file() or path.read_text() != text):
            folder.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            changed = True
        elif not install and path.exists():
            path.unlink()
            changed = True
    if not install:
        run("systemctl", "--user", "disable", "--now", f"{NAME}.timer")
        if changed:
            run("systemctl", "--user", "daemon-reload")
        return "removed" if changed else "absent"
    if changed:
        run("systemctl", "--user", "daemon-reload")
    result = run("systemctl", "--user", "enable", "--now", f"{NAME}.timer")
    if result.returncode != 0:
        return "timer start failed: " + result.stderr.strip()[-200:]
    return f"systemd timer every {INTERVAL}s ({'updated' if changed else 'unchanged'})"


def ensure_launchd(home: Path, install: bool) -> str:
    domain = f"gui/{os.getuid()}"
    path = home / PLIST
    wanted = plist_text(home)
    changed = install and (not path.is_file() or path.read_text() != wanted)
    loaded = run("launchctl", "print", f"{domain}/{LABEL}").returncode == 0
    if loaded and (changed or not install):
        run("launchctl", "bootout", f"{domain}/{LABEL}")
        loaded = False
    if not install:
        if path.exists():
            path.unlink()
            return "removed"
        return "absent"
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        (home / STATE_LOG).parent.mkdir(parents=True, exist_ok=True)
        path.write_text(wanted)
    if not loaded:
        result = run("launchctl", "bootstrap", domain, str(path))
        if result.returncode != 0:
            return "launchd start failed: " + result.stderr.strip()[-200:]
    return f"launchd agent every {INTERVAL}s ({'updated' if changed else 'unchanged'})"


def ensure_scrub_timer(home: Path, install: bool) -> str:
    if home.resolve() != Path.home().resolve():
        return "skipped: --home is not this user's home"
    if sys.platform == "darwin":
        return ensure_launchd(home, install)
    return ensure_systemd(home, install)
