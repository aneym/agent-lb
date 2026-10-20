#!/usr/bin/env python3

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def fail(message):
    print("desktop-guard-focus: unavailable: " + message, file=sys.stderr)
    return 75


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--baseline":
        os.environ["LOCAL_SEAT_FOCUS_BASELINE"] = sys.argv[2]
        sys.argv = [sys.argv[0], sys.argv[3]]
    if len(sys.argv) == 2 and sys.argv[1] != "snapshot":
        destination = Path(sys.argv[1])
        child = subprocess.Popen(
            [str(Path.home() / ".local/bin/desktop-guard-focus"), "snapshot"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = child.communicate(timeout=10)
            data = json.loads(stdout)
            if child.returncode or not data.get("front") or not isinstance(data.get("cursor"), list):
                raise ValueError("host snapshot incomplete")
            destination.write_text(json.dumps({**data, "captured_at": time.time()}) + "\n")
            return 0
        except (subprocess.TimeoutExpired, ValueError):
            os.killpg(child.pid, signal.SIGKILL) if child.poll() is None else None
            child.communicate()
            destination.write_text(json.dumps({"error": "host snapshot unavailable or timed out"}) + "\n")

            return 0
    if sys.argv[1:] != ["snapshot"]:
        return fail("telemetry runs in the lead shell; only host-captured snapshot reads are supported")
    source = os.environ.get("LOCAL_SEAT_FOCUS_BASELINE")
    if not source:
        return fail("no host baseline; launch through codex-seat from the lead shell")
    try:
        data = json.loads(Path(source).read_text())
        if data.get("error"):
            return fail(data["error"])
        print(json.dumps({**data, "source": "host-baseline", "live": False}))
        return 0
    except (OSError, ValueError) as error:
        return fail(str(error))


if __name__ == "__main__":
    sys.exit(main())
