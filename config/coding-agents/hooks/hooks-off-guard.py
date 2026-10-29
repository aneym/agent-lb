#!/usr/bin/env python3
import glob
import json
import os
import re
import sys
import unicodedata

SETTINGS_NAME = re.compile(r"(?:^|/)\.claude/settings[^/]*\.json$")
WRITES = re.compile(
    r"[^<>]>|\btee\b|\bsed\b[^|;&]*\s-i|--in-place|\b(?:mv|cp|install|dd|sponge|perl|ruby|node|python[0-9.]*|printf|echo)\b"
    r"|\bconfig\s+set\b|--settings\b"
)
BARE = re.compile(r"\bclaude\b[^|;&]*\s--(?:bare|safe-mode)\b")
SECTION = re.compile(r"^\*\*\* (Add File|Update File|Delete File): (.+)$")
MOVE = re.compile(r"^\*\*\* Move to: (.+)$")


class Refused(Exception):
    pass


def insensitive(directory):
    name = os.path.basename(directory)
    if name.swapcase() != name:
        twin = os.path.join(os.path.dirname(directory), name.swapcase())
        try:
            return os.path.samefile(directory, twin)
        except OSError:
            return False
    return sys.platform == "darwin"


def identity(path, cwd):
    real = unicodedata.normalize("NFC", os.path.realpath(os.path.join(cwd, os.path.expanduser(path.strip()))))
    anchor = real
    while not os.path.isdir(anchor) and anchor != os.path.dirname(anchor):
        anchor = os.path.dirname(anchor)
    return real.casefold() if insensitive(anchor) else real


def inode(path):
    try:
        status = os.stat(path)
    except OSError:
        return None
    return status.st_dev, status.st_ino


def known(cwd, floor_names):
    home = os.path.expanduser("~")
    floors = [os.path.join(home, ".codex/hooks.json"), os.path.join(home, ".claude/hooks/floor_hooks.py")]
    floors += [os.path.join(home, ".claude/hooks", name) for name in floor_names]
    roots, here = [home], os.path.realpath(cwd)
    while True:
        roots.append(here)
        if here == os.path.dirname(here):
            break
        here = os.path.dirname(here)
    settings = [found for root in roots for found in glob.glob(os.path.join(root, ".claude", "settings*.json"))]
    return floors, settings


def classify(path, cwd, protected):
    floors, settings = protected
    target, node = identity(path, cwd), inode(os.path.join(cwd, os.path.expanduser(path.strip())))
    for kind, paths in (("floor", floors), ("settings", settings)):
        for item in paths:
            if target == identity(item, cwd) or node is not None and node == inode(item):
                return kind
    return "settings" if SETTINGS_NAME.search(target) else None


def unique(pairs):
    keys = [key for key, _value in pairs]
    if len(keys) != len(set(keys)):
        raise Refused("it repeats a key, so the settings it would have are ambiguous")
    return dict(pairs)


def judge(text, label):
    try:
        settings = json.loads(text, object_pairs_hook=unique)
    except ValueError as error:
        raise Refused(f"the resulting {label} does not decode as JSON ({error})") from None
    from floor_hooks import hooks_off  # type: ignore[missing-import]

    found = hooks_off(settings)
    if found:
        raise Refused(f"the resulting {label} sets {', '.join(found)}, which switches every floor hook off")


def read(path, cwd):
    try:
        with open(os.path.join(cwd, os.path.expanduser(path.strip())), encoding="utf-8") as handle:
            return handle.read()
    except (OSError, ValueError):
        raise Refused(f"{path.strip()} cannot be read, so the result cannot be built") from None


def seek(lines, wanted, start, at_end):
    if not wanted:
        return len(lines)
    for norm in (lambda s: s, str.rstrip, str.strip):
        target = [norm(line) for line in wanted]
        spots = [len(lines) - len(wanted)] if at_end else range(start, len(lines) - len(wanted) + 1)
        for spot in spots:
            if spot >= start and [norm(line) for line in lines[spot : spot + len(wanted)]] == target:
                return spot
    return None


def updated(text, body, path):
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    hunks, current = [], None
    for line in body:
        if line.startswith("@@"):
            current = {"anchor": [line[2:].strip()], "old": [], "new": [], "end": []}
            hunks.append(current)
        elif line == "*** End of File" and current is not None:
            current["end"].append(line)
        elif current is not None and line[:1] in (" ", "-", "+", ""):
            if line[:1] != "+":
                current["old"].append(line[1:])
            if line[:1] != "-":
                current["new"].append(line[1:])
        else:
            raise Refused(f"the update to {path.strip()} has a line the guard cannot read")
    if not hunks:
        raise Refused(f"the update to {path.strip()} has no hunks")
    cursor = 0
    for hunk in hunks:
        if hunk["anchor"][0]:
            spot = seek(lines, hunk["anchor"], cursor, False)
            if spot is None:
                raise Refused(f"the update to {path.strip()} does not apply")
            cursor = spot + 1
        spot = seek(lines, hunk["old"], cursor, bool(hunk["end"]))
        if spot is None:
            raise Refused(f"the update to {path.strip()} does not apply")
        lines[spot : spot + len(hunk["old"])] = hunk["new"]
        cursor = spot + len(hunk["new"])
    return "\n".join(lines) + "\n"


def sections(patch):
    lines = patch.strip().split("\n")
    if not lines or lines[0].strip() != "*** Begin Patch" or lines[-1].strip() != "*** End Patch":
        raise Refused("the patch is not one the guard can read")
    found, current = [], None
    for line in lines[1:-1]:
        header, move = SECTION.match(line), MOVE.match(line)
        if header:
            current = {"head": [header[1], header[2]], "move": [], "body": []}
            found.append(current)
        elif (
            move
            and current is not None
            and current["head"][0] == "Update File"
            and not current["body"] + current["move"]
        ):
            current["move"].append(move[1])
        elif current is not None:
            current["body"].append(line)
        else:
            raise Refused("the patch has text before its first file")
    return found


def patch_reason(data, cwd, protected):
    patch = data.get("command") if isinstance(data.get("command"), str) else data.get("patch")
    if not isinstance(patch, str):
        raise Refused("the patch is not one the guard can read")
    for section in sections(patch):
        (kind, path), move = section["head"], next(iter(section["move"]), None)
        source = classify(path, cwd, protected)
        if kind == "Delete File" and source:
            raise Refused(f"it deletes {path.strip()}")
        if move and source:
            raise Refused(f"it moves {path.strip()} away")
        destination = classify(move, cwd, protected) if move else source
        if destination == "floor":
            raise Refused(f"it rewrites the floor file {(move or path).strip()}")
        if destination != "settings":
            continue
        if kind == "Add File":
            body = section["body"]
            if any(not line.startswith("+") for line in body):
                raise Refused(f"the new {path.strip()} has a line the guard cannot read")
            text = "".join(line[1:] + "\n" for line in body)
        else:
            text = updated(read(path, cwd), section["body"], path)
        judge(text, (move or path).strip())


def edit_reason(tool, data, cwd, protected):
    path = data.get("file_path") or data.get("notebook_path")
    if not isinstance(path, str) or not path:
        return
    kind = classify(path, cwd, protected)
    if kind == "floor" or kind and tool not in ("Write", "Edit", "MultiEdit"):
        raise Refused(f"it rewrites the protected file {path}")
    if kind != "settings":
        return
    if tool == "Write":
        text = data.get("content")
        if not isinstance(text, str):
            raise Refused(f"the new {path} has no readable content")
    else:
        text = read(path, cwd)
        for edit in (data.get("edits") if tool == "MultiEdit" else [data]) or []:
            old, new = edit.get("old_string"), edit.get("new_string")
            if not isinstance(old, str) or not isinstance(new, str) or not old or old not in text:
                raise Refused(f"the edit to {path} does not apply")
            text = text.replace(old, new) if edit.get("replace_all") else text.replace(old, new, 1)
    judge(text, path)


def reason(payload):
    tool, data = payload.get("tool_name"), payload.get("tool_input") or {}
    try:
        from floor_hooks import FLOOR_HOOKS, HOOKS_OFF_ENV, HOOKS_OFF_KEYS  # type: ignore[missing-import]
    except ImportError:
        return "hooks-off-guard: floor_hooks.py is missing beside this guard, so settings writes are refused until coding-agents-sync restores it."
    if not isinstance(data, dict):
        return "hooks-off-guard: the tool input is not one the guard can read."
    cwd = str(payload.get("cwd") or os.getcwd())
    if tool == "Bash":
        command = str(data.get("command", ""))
        mentioned = [name for name in HOOKS_OFF_KEYS + HOOKS_OFF_ENV if name.lower() in command.lower()]
        if BARE.search(command) or any(re.search(rf"\b{name}=|export\s+{name}\b", command) for name in HOOKS_OFF_ENV):
            return "Starting Claude Code with hooks off (--bare, --safe-mode, CLAUDE_CODE_SIMPLE or CLAUDE_CODE_SAFE_MODE) is a kept floor."
        if mentioned and WRITES.search(command):
            return f"Writing {', '.join(mentioned)} switches every floor hook off; that is a kept floor."
        return None
    protected = known(cwd, FLOOR_HOOKS)
    try:
        if tool == "apply_patch" or isinstance(data.get("command"), str) or isinstance(data.get("patch"), str):
            patch_reason(data, cwd, protected)
        else:
            edit_reason(tool, data, cwd, protected)
    except Refused as refusal:
        return f"hooks-off-guard: refused because {refusal}; Claude Code settings and floor hook files stay intact and hooks stay on (a kept floor)."
    return None


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
