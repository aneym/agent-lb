#!/usr/bin/env python3
"""Advise Workflow launches whose agent prompts lack the relayed-request guard.

Workflow subagents get the launching turn's user message relayed above their task.
Five times (09-27, 09-29 x2, 09-30, 10-01) seats followed that message instead of
their brief: wrong work, stray files, edits to another lane's plan.md. The guard
line must appear in the script (a shared prompt prefix) so every seat sees it.
Memory: workflow-relayed-user-request.md.

Adopted into agent-lb 2026-10-08 (S44, hook-dispatcher-5) from the hand-installed copy: edit this source only. It is
a floor guard, so input it cannot read (not JSON, not an object, a tool_input that is not an object, a script that is
not text) is refused, never allowed; the advice itself never blocks. A completed check ends with the receipt line
`floor-ok workflow-relay-guard.py` when the dispatcher asks for it (HOOK_FLOOR_RECEIPT); the dispatcher refuses an
exit 0 without it.
"""
import json
import os
import sys

MARK = "RELAYED-REQUEST-GUARD"
RECEIPT = "floor-ok workflow-relay-guard.py"


def refuse(why):
    sys.stderr.write("BLOCKED: workflow-relay-guard could not read the hook input (%s), so this call is refused "
                     "(the guard fails closed).\n" % why)
    sys.exit(2)


def main():
    try:
        data = json.loads(sys.stdin.read())
    except ValueError as error:
        refuse("not JSON: %s" % error)
    if not isinstance(data, dict):
        refuse("not a JSON object")
    if data.get("tool_name") != "Workflow":
        return
    ti = data.get("tool_input") or {}
    if not isinstance(ti, dict):
        refuse("tool_input is not an object")
    script = ti.get("script") or ""
    if not isinstance(script, str):
        refuse("the script is not text")
    # Inline scripts only. Saved and template runs (scriptPath, name) are factory folds launched from
    # relay turns with no live user ask; checking them stopped every fold 11:52-12:25 ET 2026-10-01.
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
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "permissionDecision": "allow", "additionalContext": advice}}))


if __name__ == "__main__":
    main()
    if os.environ.get("HOOK_FLOOR_RECEIPT"):
        print(RECEIPT)
