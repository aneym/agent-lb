#!/usr/bin/env python3
import json
import os
import re
import sys

SETTINGS_FILE = re.compile(r"(?:^|/)\.claude/settings[^/]*\.json$")
WRITES = re.compile(
    r"[^<>]>|\btee\b|\bsed\b[^|;&]*\s-i|--in-place|\b(?:mv|cp|install|dd|sponge|perl|ruby|node|python[0-9.]*|printf|echo)\b"
    r"|\bconfig\s+set\b|--settings\b"
)
BARE = re.compile(r"\bclaude\b[^|;&]*\s--(?:bare|safe-mode)\b")
PATCH_FILE = re.compile(r"^\*\*\* (Add File|Update File|Delete File|Move to): (.+)$")


def settings_path(path, cwd):
    path = os.path.join(cwd, os.path.expanduser(path.strip()))
    return any(SETTINGS_FILE.search(p) for p in (path, os.path.realpath(path)))


def patched(data, cwd):
    patch = str(data.get("command") or data.get("patch") or data.get("input") or "")
    sections, current = [], None
    for line in patch.splitlines():
        match = PATCH_FILE.match(line)
        if match:
            if match[1] == "Move to" and current is not None:
                current["targets"].append(match[2])
                try:
                    with open(os.path.join(cwd, os.path.expanduser(current["targets"][0].strip()))) as handle:
                        current["added"].append(handle.read())
                except OSError:
                    pass
                continue
            current = {"targets": [match[2]], "added": []}
            sections.append(current)
        elif current is not None and line.startswith("+"):
            current["added"].append(line[1:])
    return "\n".join(
        text
        for section in sections
        if any(settings_path(t, cwd) for t in section["targets"])
        for text in section["added"]
    )


def edited(tool, data):
    path = os.path.expanduser(str(data.get("file_path") or data.get("notebook_path") or ""))
    if not path or not any(SETTINGS_FILE.search(p) for p in (path, os.path.realpath(path))):
        return None
    if tool == "Write":
        return data.get("content", "")
    try:
        with open(path) as handle:
            text = handle.read()
    except OSError:
        text = ""
    edits = data.get("edits") if tool == "MultiEdit" else [data]
    for edit in edits or []:
        old, new = edit.get("old_string", ""), edit.get("new_string", "")
        text = text.replace(old, new) if edit.get("replace_all") else text.replace(old, new, 1)
    return text


def reason(payload):
    tool, data = payload.get("tool_name"), payload.get("tool_input") or {}
    try:
        from floor_hooks import HOOKS_OFF_ENV, HOOKS_OFF_KEYS, hooks_off  # type: ignore[missing-import]
    except ImportError:
        return "hooks-off-guard: floor_hooks.py is missing beside this guard, so settings writes are refused until coding-agents-sync restores it."
    names = HOOKS_OFF_KEYS + HOOKS_OFF_ENV
    if tool == "Bash":
        command = str(data.get("command", ""))
        mentioned = [name for name in names if name.lower() in command.lower()]
        if BARE.search(command) or any(re.search(rf"\b{name}=|export\s+{name}\b", command) for name in HOOKS_OFF_ENV):
            return "Starting Claude Code with hooks off (--bare, --safe-mode, CLAUDE_CODE_SIMPLE or CLAUDE_CODE_SAFE_MODE) is a kept floor."
        if mentioned and WRITES.search(command):
            return f"Writing {', '.join(mentioned)} switches every floor hook off; that is a kept floor."
        return None
    if tool == "apply_patch":
        text = patched(data, str(payload.get("cwd") or os.getcwd()))
        mentioned = [name for name in names if name.lower() in text.lower()]
        return (
            f"Patching {', '.join(mentioned)} into Claude Code settings switches every floor hook off; that is a kept floor."
            if mentioned
            else None
        )
    text = edited(tool, data)
    if text is None:
        return None
    try:
        found = hooks_off(json.loads(text))
    except ValueError:
        found = [name for name in names if name in text]
    return (
        f"Setting {', '.join(found)} in Claude Code settings switches every floor hook off; that is a kept floor."
        if found
        else None
    )


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    why = reason(payload) if isinstance(payload, dict) else None
    if why:
        decision = {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": why}
        print(json.dumps({"hookSpecificOutput": decision}))
        print(why, file=sys.stderr)
        return 2
    if os.environ.get("HOOK_FLOOR_RECEIPT"):
        print("floor-ok hooks-off-guard.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
