#!/usr/bin/env python3
import fcntl
import json
import os
import re
import shlex
import sys
import time

MESSAGE = "hands-nudge (warn only): 15 hands-shaped Bash calls in this Opus lead; send builds, tests, probes and landing to gpt-implementer (seat-run) and keep Opus for decisions (models.md, Teams and workflows)."


def hands_command(command):
    if "OPUS-REASON" in command:
        return False
    for part in re.split(r"&&|\|\||[;\n|]", command):
        words = shlex.split(part)
        while words and ("=" in words[0] or words[0] in ("env", "command", "exec")):
            words.pop(0)
        if not words:
            continue
        name = os.path.basename(words[0])
        args = words[1:]
        if name in {
            "pytest",
            "py.test",
            "psql",
            "playwright",
            "curl",
            "node",
            "npx",
            "pnpm",
            "make",
            "cmake",
            "ninja",
            "xcodebuild",
            "swift",
            "test-guard",
            "check",
        }:
            return True
        if (
            name in {"npm", "uv", "cargo", "go", "bun", "yarn"}
            and args
            and args[0] in {"run", "test", "build", "check"}
        ):
            return True
        if name in {"python", "python3"} and args and (args[0] not in {"-c", "--version", "-V"}):
            return True
        if name == "git" and any(arg in {"rebase", "commit", "push"} for arg in args):
            return True
    return False


def lead_model(path):
    with open(path, "rb") as handle:
        handle.seek(0, 2)
        handle.seek(max(0, handle.tell() - 65536))
        tail = handle.read(65536)
    for line in reversed(tail.splitlines()):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("type") == "assistant":
            message = entry.get("message", {})
            model = message.get("model") if isinstance(message, dict) else None
            return isinstance(model, str) and any(name in model.lower() for name in ("opus", "fable"))
    return False


def main():
    try:
        payload = json.load(sys.stdin)
        if payload.get("agent_id") or payload.get("agent_type") or payload.get("subagent_id"):
            return
        command = payload.get("tool_input", {}).get("command")
        session = payload.get("session_id")
        if not isinstance(command, str) or not isinstance(session, str) or not re.fullmatch(r"[\w-]{1,200}", session):
            return
        if not hands_command(command) or not lead_model(payload["transcript_path"]):
            return
        directory = os.path.expanduser("~/.cache/claude-bash-hands-nudge")
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, session), "a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.seek(0)
            try:
                state = json.load(handle)
            except ValueError:
                state = {}
            now = time.time()
            count = 0 if now - state.get("last", 0) > 1800 else state.get("count", 0)
            count += 1
            warned = state.get("warned", 0)
            fire = count >= 15 and now - warned >= 7200
            if fire:
                warned = now
            handle.seek(0)
            handle.truncate()
            json.dump({"count": count, "last": now, "warned": warned}, handle)
        if fire:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": MESSAGE}}))
    except Exception:
        return


if __name__ == "__main__":
    main()
