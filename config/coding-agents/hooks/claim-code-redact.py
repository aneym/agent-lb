import json
import re
import sys

from claim_codes import FILTER, output_fields  # type: ignore[missing-import]

SOURCES = re.compile(r"rac_|agent_create|claim|one\.rails\.so|/mcp\b", re.I)
DETACHED = re.compile(r"(?<![&>|])&(?![&>])|\b(nohup|setsid|disown|exec)\b")


def emit(event, **fields):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, **fields}}))


def wrapped_bash(command):
    return (
        f'set -o pipefail; __cc_cwd=$(mktemp); {{ {command}\n__cc_rc=$?; pwd -P > "$__cc_cwd"; exit $__cc_rc; }} 2>&1 | {FILTER}; '
        '__cc_rc=$?; __cc_dir=$(cat "$__cc_cwd" 2>/dev/null); command -p rm -f "$__cc_cwd"; '
        '[ -n "$__cc_dir" ] && cd "$__cc_dir"; (exit $__cc_rc)'
    )


def wrapped_monitor(command):
    return f"set -o pipefail; {{ {command}\n}} 2> >({FILTER} >&2) | {FILTER}"


def wrap(data):
    tool_input = data.get("tool_input") or {}
    command = tool_input.get("command")
    if not isinstance(command, str) or not SOURCES.search(command) or DETACHED.search(command):
        return
    rewrite = wrapped_monitor if data.get("tool_name") == "Monitor" else wrapped_bash
    emit("PreToolUse", updatedInput={**tool_input, "command": rewrite(command)})


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(data, dict):
        return 0
    event = data.get("hook_event_name")
    if event == "PreToolUse" and data.get("tool_name") in ("Bash", "Monitor"):
        wrap(data)
    elif event == "PostToolUse":
        fields = output_fields(data)
        if fields:
            emit("PostToolUse", **fields)
    return 0


if __name__ == "__main__":
    sys.exit(main())
