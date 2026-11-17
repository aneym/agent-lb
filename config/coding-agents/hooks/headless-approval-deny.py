#!/usr/bin/env python3
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

from headless_run import headless, refusal  # type: ignore[missing-import]

LOG = os.path.join(os.path.expanduser("~"), ".claude", "hooks", "state", "headless-approvals.jsonl")


def logged(payload):
    tool_input = json.dumps(payload.get("tool_input"), sort_keys=True, default=str).encode()
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "thread": os.environ.get("T3_THREAD_ID", ""),
        "session": payload.get("session_id"),
        "tool": payload.get("tool_name"),
        "input_sha256": hashlib.sha256(tool_input).hexdigest()[:16],
        "decision": "deny",
    }
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as handle:
            handle.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = {}
    if not isinstance(payload, dict) or not headless():
        return 0
    logged(payload)
    answer = {"behavior": "deny", "message": refusal()}
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": answer}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
