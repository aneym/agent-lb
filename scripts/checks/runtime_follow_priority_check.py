#!/usr/bin/env python3
from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/install-runtime-follow.sh"), "--print"],
        capture_output=True,
        check=True,
        timeout=30,
    )
    plist = plistlib.loads(result.stdout)
    if plist.get("LowPriorityIO") or plist.get("Nice", 0) > 0:
        raise AssertionError("runtime follower must not run at throttled I/O or CPU priority")
    if plist["Label"] != "com.aneyman.agent-lb-follow":
        raise AssertionError("installer rendered a different job")
    if plist["StartInterval"] != 300 or not plist["RunAtLoad"]:
        raise AssertionError("runtime follower lost its scheduled or initial tick")
    if plist["ProgramArguments"][-1] != "tick":
        raise AssertionError("runtime follower does not execute tick")
    print("PASS runtime-follow priority: rendered job uses normal I/O and CPU priority; 300s ticks retained")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
