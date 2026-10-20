#!/usr/bin/env python3
import json
import re
import sys


def main():
    try:
        data = json.load(sys.stdin)
        command = (data.get("tool_input") or {}).get("command", "")
        if re.search(r"(?:^|[\s/])herdr\s+(?:agent\s+prompt|pane\s+(?:send-text|run))\b", command):
            print(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "additionalContext": "herdr-send-guard: warn: direct input may target a Claude pane. Use lane-post for agent messaging; never send into a Claude input bar.",
                        }
                    }
                )
            )
    except (ValueError, TypeError):
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
