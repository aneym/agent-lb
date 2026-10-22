#!/usr/bin/env python3
import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path[:0] = [os.path.dirname(os.path.abspath(__file__)), os.path.dirname(os.path.realpath(__file__))]

from hook_prefilters import pf_poll_loop  # type: ignore[missing-import]
from hook_wide_scan_shell import join_continuations, shell_tokens, strip_heredocs, substitutions  # type: ignore[missing-import]

MESSAGE = (
    "Waits rule (warn only): short-sleep poll loop. No tool call should block past 270 s; "
    "avoid watchers/poll loops. Use run_in_background (exit notification), Monitor with factory-wait, "
    "or seat-run --wait."
)
SEPARATOR = re.compile(r"^[;&|()<>\n]+$")
SECONDS = re.compile(r"(?:\d+(?:\.\d+)?|\.\d+)$")
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}


def loops(text, depth=0):
    if depth > 16:
        return []
    text, bodies = strip_heredocs(join_continuations(text))
    found = []
    for body in bodies + substitutions(text):
        found.extend(loops(body, depth + 1))
    tokens = shell_tokens(text)
    stack, segment = [], []

    def command(words):
        if not words:
            return
        name = words[0].rsplit("/", 1)[-1]
        if name == "sleep" and len(words) > 1 and SECONDS.fullmatch(words[1]) and float(words[1]) < 60:
            for frame in stack:
                if frame[1]:
                    frame[2] = True
        if name == "eval":
            found.extend(loops(" ".join(words[1:]), depth + 1))
        elif name in SHELLS:
            for index, word in enumerate(words[1:], 1):
                if word.startswith("-") and "c" in word and index + 1 < len(words):
                    found.extend(loops(words[index + 1], depth + 1))
                    break

    boundary = True
    for index, token in enumerate(tokens + [";"]):
        if SEPARATOR.fullmatch(token):
            command(segment)
            segment = []
            boundary = True
        elif boundary and token in ("while", "until", "for", "select"):
            stack.append([index, False, False])
        elif boundary and token == "do" and stack:
            stack[-1][1] = True
        elif boundary and token == "done" and stack:
            start, _body, short = stack.pop()
            if short:
                found.append(
                    json.dumps(
                        [";" if word.isspace() else word for word in tokens[start : index + 1]], ensure_ascii=False
                    )
                )
        elif boundary and token in ("if", "then", "else", "elif", "!", "{"):
            continue
        else:
            segment.append(token)
            boundary = False
    return found


def main():
    try:
        payload = json.load(sys.stdin)
        if not pf_poll_loop(payload):
            return
        matches = loops(payload["tool_input"]["command"])
        if not matches:
            return
        state = Path.home() / ".claude" / "state" / "poll-loop-warn"
        state.mkdir(parents=True, exist_ok=True)
        first = False
        for loop in set(matches):
            key = hashlib.sha256(json.dumps([payload.get("session_id"), loop]).encode()).hexdigest()
            try:
                with (state / key).open("x"):
                    pass
                first = True
            except FileExistsError:
                pass
        if first:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": MESSAGE}}))
    except (OSError, ValueError, TypeError, KeyError):
        return


if __name__ == "__main__":
    main()
