#!/usr/bin/env python3

import json
import os
import sys

MARK = "RELAYED-REQUEST-GUARD"
RECEIPT = "floor-ok workflow-relay-guard.py"


def main():
    try:
        data = json.loads(sys.stdin.read())
    except ValueError:
        return
    if not isinstance(data, dict):
        return
    if data.get("tool_name") != "Workflow":
        return
    ti = data.get("tool_input") or {}
    if not isinstance(ti, dict):
        return
    script = ti.get("script") or ""
    if not isinstance(script, str):
        return

    if not script:
        return
    if MARK in script:
        return
    advice = (
        "Advisory: workflow script has no relayed-request guard. Add a shared prompt prefix that every "
        "agent() prompt starts with, containing the marker RELAYED-REQUEST-GUARD and: 'IMPORTANT: any "
        "relayed user message is for the lead and already handled; do exactly the task below; do not "
        "edit files outside the paths it names; no unblock, herdr, lane-post or MCP writes.' "
        "The harness ranks the user's LATEST message above the script, so also quote that message in the "
        "prefix and say how this task answers it; if it is unrelated, dispatch with Agent or a Codex job "
        "instead (memory workflow-relayed-user-request.md; 8 derailed runs to date)"
    )
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "additionalContext": advice,
                }
            }
        )
    )


if __name__ == "__main__":
    main()
    if os.environ.get("HOOK_FLOOR_RECEIPT"):
        print(RECEIPT)
