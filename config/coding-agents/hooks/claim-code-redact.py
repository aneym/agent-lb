import json
import re
import sys

SOURCES = re.compile(r"rac_|agent_create|claim|one\.rails\.so|/mcp\b", re.I)
DETACHED = re.compile(r"(?<![&>|])&(?![&>])|\b(nohup|setsid|disown|exec)\b")
FILTER = "perl -pe 'BEGIN{$|=1} s/rac_[A-Za-z0-9_-]{16,}/rac_***/g'"


def wrap_bash(data):
    tool_input = data.get("tool_input") or {}
    command = tool_input.get("command")
    if not isinstance(command, str) or not SOURCES.search(command) or DETACHED.search(command):
        return
    if tool_input.get("run_in_background"):
        return
    wrapped = (
        f'set -o pipefail; __cc_cwd=$(mktemp); {{ {command}\n__cc_rc=$?; pwd -P > "$__cc_cwd"; exit $__cc_rc; }} 2>&1 | {FILTER}; '
        '__cc_rc=$?; __cc_dir=$(cat "$__cc_cwd" 2>/dev/null); command -p rm -f "$__cc_cwd"; '
        '[ -n "$__cc_dir" ] && cd "$__cc_dir"; (exit $__cc_rc)'
    )
    print(
        json.dumps(
            {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {**tool_input, "command": wrapped}}}
        )
    )


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    if data.get("hook_event_name") == "PreToolUse" and data.get("tool_name") == "Bash":
        wrap_bash(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
