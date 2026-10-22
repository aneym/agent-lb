import json, sys
data = json.load(sys.stdin)
if "Herdr Shell" in (data.get("tool_input") or {}).get("command", ""):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": "BLOCKED: not on the host"}}))
