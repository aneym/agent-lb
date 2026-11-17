import json
import re
import sys

CODE = re.compile(r"rac_[A-Za-z0-9_-]{16,}")
MASK = "rac_***"
SOURCES = re.compile(r"rac_|agent_create|claim|one\.rails\.so|/mcp\b", re.I)
DETACHED = re.compile(r"(?<![&>|])&(?![&>])|\b(nohup|setsid|disown|exec)\b")
FILTER = "perl -pe 'BEGIN{$|=1} s/rac_[A-Za-z0-9_-]{16,}/rac_***/g'"


def emit(event, **fields):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, **fields}}))


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
    emit("PreToolUse", updatedInput={**tool_input, "command": wrapped})


def scrub(value):
    if isinstance(value, str):
        return CODE.sub(MASK, value)
    if isinstance(value, list):
        return [scrub(item) for item in value]
    if isinstance(value, dict):
        return {key: scrub(item) for key, item in value.items()}
    return value


def redact_mcp(data):
    response = data.get("tool_response")
    if response is None or not CODE.search(json.dumps(response)):
        return
    emit("PostToolUse", updatedMCPToolOutput=scrub(response))


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    event = data.get("hook_event_name")
    tool = data.get("tool_name") or ""
    if event == "PreToolUse" and tool == "Bash":
        wrap_bash(data)
    elif event == "PostToolUse" and tool.startswith("mcp__"):
        redact_mcp(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
