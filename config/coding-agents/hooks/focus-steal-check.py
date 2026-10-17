#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

FOCUS_STEAL_RC = 6


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def seconds(stamp):
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(timezone.utc).isoformat(
            timespec="seconds")
    except ValueError:
        return None


def guard():
    tool = os.environ.get("DESKTOP_GUARD_FOCUS") or str(Path.home() / ".local/bin/desktop-guard-focus")
    return tool if os.path.exists(tool) else None


def steals(since):
    tool = guard()
    if not tool or not since:
        return []
    try:
        done = subprocess.run([tool, "check", "--since", since], capture_output=True, text=True, timeout=10)
        rows = (json.loads(done.stdout).get("rows") or []) if done.returncode == 1 else []
    except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired):
        return []
    pane = os.environ.get("HERDR_PANE_ID")
    return [r for r in rows if not pane or not r.get("pane") or r.get("pane") == pane]


def message(name, since, rows):
    tools = ",".join(sorted({str(r.get("tool") or "?") for r in rows}))
    return (f"FOCUS-STEAL {name}: {len(rows)} focus steal(s) on Alex's desktop during this Studio-local run "
            f"({tools}); the run failed (Alex, 2026-10-16). Rows: desktop-guard-focus check --since {since}")


def first_timestamp(path):
    try:
        with open(path) as f:
            for line in f:
                try:
                    stamp = json.loads(line).get("timestamp")
                except (ValueError, AttributeError):
                    continue
                if stamp:
                    return seconds(stamp)
    except OSError:
        return None
    return None


def local_seat(agent_type):
    name = agent_type.lower()
    return name.endswith("-local") or "-local-" in name


def subagent_stop():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    agent_type = str(payload.get("agent_type") or payload.get("subagent_type") or "")
    if payload.get("stop_hook_active") or not local_seat(agent_type):
        return 0
    since = first_timestamp(str(payload.get("agent_transcript_path") or ""))
    rows = steals(since)
    if rows:
        print(json.dumps({"decision": "block", "reason": message(agent_type, since, rows) +
                          ". End your report with `FAILED: focus steal` and the rows; do not claim success."}))
    return 0


def main(argv):
    if argv[:1] == ["subagent-stop"]:
        return subagent_stop()
    if len(argv) >= 2 and argv[0] == "--since":
        rows = steals(seconds(argv[1]))
        if rows:
            print(message(argv[2] if len(argv) > 2 else "seat", argv[1], rows), file=sys.stderr)
            return FOCUS_STEAL_RC
        return 0
    if argv[:1] == ["now"]:
        print(now())
        return 0
    print("usage: focus-steal-check.py now | --since <iso> [name] | subagent-stop < payload", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
