#!/usr/bin/env python3
from __future__ import annotations

import os
import plistlib
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE_LABEL = "com.aneyman.agent-lb-follow-priority-probe"
SCHEDULING_KEYS = ("ProcessType", "Nice", "LowPriorityIO", "LowPriorityBackgroundIO")
UNCLAMPED_PRI = 31


def rendered_job() -> dict:
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/install-runtime-follow.sh"), "--print"],
        capture_output=True,
        check=True,
        timeout=30,
    )
    return plistlib.loads(result.stdout)


def descendant_priority(job: dict) -> int:
    domain = f"gui/{os.getuid()}"
    with tempfile.TemporaryDirectory() as tmp:
        marker = Path(tmp) / "child.pid"
        probe = {key: job[key] for key in SCHEDULING_KEYS if key in job}
        probe |= {
            "Label": PROBE_LABEL,
            "ProgramArguments": ["/bin/sh", "-c", f'/bin/sleep 60 & echo $! > "{marker}"; wait'],
            "RunAtLoad": True,
        }
        plist = Path(tmp) / f"{PROBE_LABEL}.plist"
        plist.write_bytes(plistlib.dumps(probe))
        subprocess.run(["launchctl", "bootout", f"{domain}/{PROBE_LABEL}"], capture_output=True, timeout=30)
        bootstrapped = False
        try:
            subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=True, capture_output=True, timeout=30)
            bootstrapped = True
            deadline = time.monotonic() + 30
            while not (marker.exists() and marker.read_text().strip()):
                if time.monotonic() > deadline:
                    raise AssertionError("probe job never started its child")
                time.sleep(0.2)
            pid = marker.read_text().strip()
            out = subprocess.run(["ps", "-o", "pri=", "-p", pid], capture_output=True, text=True, check=True)
            return int(out.stdout.strip())
        finally:
            cleanup = subprocess.run(
                ["launchctl", "bootout", f"{domain}/{PROBE_LABEL}"], capture_output=True, timeout=30
            )
            if bootstrapped or cleanup.returncode != 3:
                cleanup.check_returncode()


def main() -> int:
    plist = rendered_job()
    if plist.get("LowPriorityIO") or plist.get("Nice", 0) > 0:
        raise AssertionError("runtime follower must not run at throttled I/O or CPU priority")
    if plist["Label"] != "com.aneyman.agent-lb-follow":
        raise AssertionError("installer rendered a different job")
    if plist["StartInterval"] != 300 or not plist["RunAtLoad"]:
        raise AssertionError("runtime follower lost its scheduled or initial tick")
    if plist["ProgramArguments"][-1] != "tick":
        raise AssertionError("runtime follower does not execute tick")
    pri = descendant_priority(plist)
    if pri < UNCLAMPED_PRI:
        raise AssertionError(
            f"a process spawned under the rendered follower job runs at pri {pri} (< {UNCLAMPED_PRI}): "
            "launchd clamps its QoS, so the lb-restart standby it spawns starves past the boot gate"
        )
    print(
        f"PASS runtime-follow priority: launchd child of the rendered job runs unclamped (pri {pri}); "
        "normal I/O and CPU priority; 300s ticks retained"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
