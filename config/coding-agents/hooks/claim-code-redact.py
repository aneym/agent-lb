import json
import re
import sys

from claim_codes import FILTER, post  # type: ignore[missing-import]

SOURCES = re.compile(r"rac_|agent_create|claim|one\.rails\.so|/mcp\b", re.I)
DETACHED = re.compile(r"(?<![&>|])&(?![&>])|\b(nohup|setsid|disown|exec)\b")


def emit(event, **fields):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, **fields}}))


def wrapped_bash(command):
    return (
        f"set -o pipefail; {{ __cc_dir=$({{ {{ {{ {command}\n}} 3>&- 4>&-; __cc_rc=$?; pwd -P >&3; exit $__cc_rc; }} "
        f"2>&1 | {FILTER} >&4; }} 3>&1); __cc_rc=$?; }} 4>&1; "
        '[ -n "$__cc_dir" ] && cd "$__cc_dir"; (exit $__cc_rc)'
    )


def wrapped_monitor(command):
    return f"set -o pipefail; {{ {command}\n}} 2> >({FILTER} >&2) | {FILTER}"


def wrap(data, every):
    tool_input = data.get("tool_input") or {}
    command = tool_input.get("command")
    if not isinstance(command, str):
        return
    if data.get("tool_name") == "Monitor":
        rewrite = wrapped_monitor
    elif DETACHED.search(command) or not (every or tool_input.get("run_in_background") or SOURCES.search(command)):
        return
    else:
        rewrite = wrapped_bash
    decision = {"permissionDecision": "allow"} if every else {}
    emit("PreToolUse", **decision, updatedInput={**tool_input, "command": rewrite(command)})


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(data, dict):
        return 0
    event = data.get("hook_event_name")
    if event == "PreToolUse" and data.get("tool_name") in ("Bash", "Monitor"):
        wrap(data, "codex" in sys.argv[1:])
    elif event == "PostToolUse":
        post(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
