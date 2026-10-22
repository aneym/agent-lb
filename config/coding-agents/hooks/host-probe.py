#!/usr/bin/env python3

import argparse
import json
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.parse_args()
    started = time.monotonic()
    captured_at = datetime.now(timezone.utc).isoformat()
    commands = {
        "ps": ["/bin/ps", "-axo", "pid=,ppid=,comm="],
        "pgrep": ["/usr/bin/pgrep", "-l", "WindowServer|desktop-guard|codex|Claude"],
        "sysctl": ["/usr/sbin/sysctl", "vm.swapusage", "kern.memorystatus_vm_pressure_level"],
        "memory_pressure": ["/usr/bin/memory_pressure", "-Q"],
        "focus": [str(Path.home() / ".local/bin/desktop-guard-focus"), "snapshot"],
    }
    readings: dict[str, Any] = {}
    running = []
    try:
        for name, command in commands.items():
            stdout = tempfile.TemporaryFile()
            stderr = tempfile.TemporaryFile()
            try:
                child = subprocess.Popen(command, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL)
            except OSError as error:
                stdout.close()
                stderr.close()
                readings[name] = {"error": str(error)}
                continue
            running.append((name, child, stdout, stderr))
        for name, child, stdout, stderr in running:
            timed_out = False
            try:
                child.wait(timeout=max(0, 10 - (time.monotonic() - started)))
            except subprocess.TimeoutExpired:
                timed_out = True
                child.kill()
                child.wait()
            stdout.seek(0)
            stderr.seek(0)
            output = stdout.read(65537)
            errors = stderr.read(4097)
            reading = {
                "rc": child.returncode,
                "timed_out": timed_out,
                "stdout": output[:65536].decode(errors="replace"),
                "stderr": errors[:4096].decode(errors="replace"),
                "truncated": len(output) > 65536 or len(errors) > 4096,
            }
            if name == "focus" and child.returncode == 0:
                try:
                    reading["data"] = json.loads(reading["stdout"])
                except ValueError:
                    reading["error"] = "invalid focus JSON"
            readings[name] = reading
    finally:
        for _, child, stdout, stderr in running:
            if child.poll() is None:
                child.kill()
                child.wait()
            stdout.close()
            stderr.close()
    print(
        json.dumps(
            {
                "live": True,
                "captured_at": captured_at,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "readings": readings,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
