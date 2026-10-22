import json, sys
data = json.load(sys.stdin)
if "gh pr merge" in (data.get("tool_input") or {}).get("command", ""):
    sys.stderr.write("BLOCKED: merge through the queue\n")
    sys.exit(2)
