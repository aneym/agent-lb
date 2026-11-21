from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import time

from app.core.browser_profiles import BrowserProfile, registry_path, user_data_dir
from app.core.cdp_page import CdpError, cdp_up


class LaunchError(OSError):
    pass


def chrome_argv(profile: BrowserProfile, url: str) -> list[str]:
    return [
        os.getenv("AGENT_LB_CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        f"--user-data-dir={user_data_dir(profile)}",
        f"--remote-debugging-port={profile.cdp_port}",
        "--remote-debugging-address=127.0.0.1",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        url,
    ]


def in_gui_session() -> bool:
    try:
        result = subprocess.run(["launchctl", "managername"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.stdout.strip() == "Aqua"


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=30)


def _gui_launch(profile: BrowserProfile, url: str) -> None:
    domain = f"gui/{os.getuid()}"
    label = f"com.agent-lb.chrome.{profile.cdp_port}"
    if "state = running" in _launchctl("print", f"{domain}/{label}").stdout:
        return
    _launchctl("bootout", f"{domain}/{label}")
    folder = registry_path().parent / "chrome-jobs"
    folder.mkdir(parents=True, exist_ok=True)
    plist = folder / f"{label}.plist"
    job = {
        "Label": label,
        "ProgramArguments": chrome_argv(profile, url),
        "RunAtLoad": True,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }
    plist.write_bytes(plistlib.dumps(job))
    loaded = _launchctl("bootstrap", domain, str(plist))
    if loaded.returncode != 0:
        raise LaunchError(
            f"could not start the {profile.email} Chrome in the {domain} launchd domain"
            f" (launchctl bootstrap exited {loaded.returncode}: {loaded.stderr.strip()[:160]});"
            " a profile Chrome outside the Mac's GUI session cannot reach the login keychain and drops its cookies,"
            " so run this from a launchd job in that domain or with someone signed in to the Mac's desktop"
        )


def launch_profile(profile: BrowserProfile, url: str) -> None:
    if sys.platform == "darwin" and not in_gui_session():
        _gui_launch(profile, url)
        return
    subprocess.Popen(
        chrome_argv(profile, url), start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def start_chrome(profile: BrowserProfile, url: str = "about:blank", seconds: float = 20) -> bool:
    if cdp_up(profile.cdp_port):
        return False
    launch_profile(profile, url)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not cdp_up(profile.cdp_port):
        time.sleep(0.25)
    if not cdp_up(profile.cdp_port):
        raise CdpError(f"the {profile.email} Chrome did not open DevTools on {profile.cdp_port}")
    return True
