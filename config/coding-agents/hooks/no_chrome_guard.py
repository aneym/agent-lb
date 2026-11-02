import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import approval_record  # type: ignore[missing-import]
from no_chrome_classify import classify  # type: ignore[missing-import]
from no_chrome_doubt import kind as doubt_kind  # type: ignore[missing-import]
from no_chrome_shell import Doubt  # type: ignore[missing-import]

RETIRED_LANE_BROWSER = re.compile(r"(?<![\w.-])lane-browser(?![\w.-])")


def allow():
    if os.environ.get("HOOK_FLOOR_RECEIPT"):
        print("floor-ok no-chrome-guard.sh")
    return 0


def refuse(reason):
    print(
        "BLOCKED (floor: the owner's own browser and accounts): no-chrome-guard "
        + reason
        + ", so the call is refused (the guard fails closed).",
        file=sys.stderr,
    )
    return 2


def approved(command):
    match = re.search(r"#[ \t]*(?:owner|alex)-approval:[ \t]*([^\s]+)[ \t]*$", command.splitlines()[-1])
    if not match:
        return False
    reason = approval_record.refusal(match[1], "browser")
    if reason:
        print("approval record refused: " + reason, file=sys.stderr)
    return not reason


def main():
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("object required")
        tool = payload.get("tool_input")
        if tool is not None and not isinstance(tool, dict):
            raise ValueError("tool_input object required")
        command = (tool or {}).get("command")
        if command is None:
            command = ""
        if not isinstance(command, str) or "\0" in command:
            raise ValueError("command string required")
    except (OSError, ValueError):
        return refuse("could not read the hook input")
    if not command:
        return allow()
    try:
        kind = classify(command)
    except (Doubt, ValueError, OSError):
        kind = doubt_kind(command)
    if kind == "doubt":
        home = Path(os.environ.get("FACTORY_HOME") or Path.home() / ".agent-rails/factory") / "state"
        session = re.sub(r"[^A-Za-z0-9_-]", "_", str(payload.get("session_id") or "none"))
        try:
            directory = home / "no-chrome-guard-doubt"
            directory.mkdir(parents=True, exist_ok=True)
            with (home / "no-chrome-guard-doubt.jsonl").open("a") as log:
                log.write(
                    json.dumps(
                        {
                            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "decision": "pass-unparsed",
                            "session": session,
                            "command": command,
                        }
                    )
                    + "\n"
                )
            with (directory / session).open("x"):
                pass
            print(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "additionalContext": "no-chrome-guard could not fully parse this command and found no browser action in it, so it ran. Warned once per session; unparsed calls are logged to factory state/no-chrome-guard-doubt.jsonl.",
                        }
                    }
                )
            )
        except OSError:
            pass
        return allow()
    if kind in {"none", "pc"} or approved(command):
        return allow()
    if RETIRED_LANE_BROWSER.search(command):
        return refuse(
            "no longer trusts the retired lane-browser; use lane-harness <session> shot <url> <name.png>"
            " or a lane-harness <session> script for agent-owned headless pages"
        )
    return refuse("requires an alex-approval record whose T3 evidence, quote and Covers: browser line check out")


if __name__ == "__main__":
    sys.exit(main())
