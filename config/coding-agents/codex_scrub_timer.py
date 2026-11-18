from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from policy_layout import POLICY_DIR  # type: ignore[missing-import]

UNIT_DIR = Path(".config/systemd/user")
STATE_LOG = Path(".local/state/codex-rollout-scrub/scrub.log")


@dataclass(frozen=True)
class Job:
    name: str
    interval: int
    flags: tuple[str, ...]
    first_run: str
    accuracy: str

    @property
    def label(self) -> str:
        return f"com.aneym.{self.name}"

    @property
    def plist(self) -> Path:
        return Path("Library/LaunchAgents") / f"{self.label}.plist"


JOBS = (
    Job("codex-rollout-scrub", 30, ("--quiet",), "OnBootSec=1min", "5s"),
    Job("codex-rollout-scrub-full", 3600, ("--full", "--quiet"), "OnActiveSec=15min", "5min"),
)


def scrub_command(home: Path, job: Job) -> list[str]:
    return [sys.executable, str(home / POLICY_DIR / "codex_rollout_scrub.py"), *job.flags]


def systemd_units(home: Path, job: Job) -> dict[str, str]:
    command = " ".join(scrub_command(home, job))
    what = "Mask claim codes in Codex rollouts" + (", ad hoc homes included" if "--full" in job.flags else "")
    return {
        f"{job.name}.service": (
            f"[Unit]\nDescription={what}\n\n"
            f"[Service]\nType=oneshot\nNice=10\nIOSchedulingClass=idle\nExecStart={command}\n"
        ),
        f"{job.name}.timer": (
            f"[Unit]\nDescription={what}\n\n"
            f"[Timer]\n{job.first_run}\nOnUnitActiveSec={job.interval}s\nAccuracySec={job.accuracy}\n\n"
            "[Install]\nWantedBy=timers.target\n"
        ),
    }


def plist_text(home: Path, job: Job) -> str:
    args = "".join(f"<string>{part}</string>" for part in scrub_command(home, job))
    log = home / STATE_LOG
    at_load = "<true/>" if job.interval < 60 else "<false/>"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{job.label}</string>
  <key>ProgramArguments</key>
  <array>{args}</array>
  <key>EnvironmentVariables</key>
  <dict><key>HOME</key><string>{home}</string></dict>
  <key>StartInterval</key><integer>{job.interval}</integer>
  <key>RunAtLoad</key>{at_load}
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""


def run(*command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), capture_output=True, text=True, timeout=60)


def ensure_systemd(home: Path, job: Job, install: bool) -> str:
    folder = home / UNIT_DIR
    units = systemd_units(home, job)
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
        run("systemctl", "--user", "disable", "--now", f"{job.name}.timer")
        if changed:
            run("systemctl", "--user", "daemon-reload")
        return "removed" if changed else "absent"
    if changed:
        run("systemctl", "--user", "daemon-reload")
    result = run("systemctl", "--user", "enable", "--now", f"{job.name}.timer")
    if result.returncode != 0:
        return "timer start failed: " + result.stderr.strip()[-200:]
    return f"systemd timer every {job.interval}s ({'updated' if changed else 'unchanged'})"


def ensure_launchd(home: Path, job: Job, install: bool) -> str:
    domain = f"gui/{os.getuid()}"
    path = home / job.plist
    wanted = plist_text(home, job)
    changed = install and (not path.is_file() or path.read_text() != wanted)
    loaded = run("launchctl", "print", f"{domain}/{job.label}").returncode == 0
    if loaded and (changed or not install):
        run("launchctl", "bootout", f"{domain}/{job.label}")
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
    return f"launchd agent every {job.interval}s ({'updated' if changed else 'unchanged'})"


def ensure_scrub_timer(home: Path, install: bool) -> str:
    if home.resolve() != Path.home().resolve():
        return "skipped: --home is not this user's home"
    ensure = ensure_launchd if sys.platform == "darwin" else ensure_systemd
    return "; ".join(f"{job.name}: {ensure(home, job, install)}" for job in JOBS)
