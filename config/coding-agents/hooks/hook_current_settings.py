import json
import os
import re


def current_entry(event, key):
    home = os.path.expanduser("~")
    path = os.path.join(home, ".claude", "settings.json")
    with open(path, encoding="utf-8") as handle:
        settings = json.load(handle)
    groups = settings.get("hooks", {})
    if not isinstance(groups, dict):
        raise ValueError("current settings hooks are not an object")
    hooks = []
    for group in groups.get(event, []):
        matcher = group.get("matcher", "")
        if matcher not in ("", "*") and not re.fullmatch(matcher, key):
            continue
        for hook in group.get("hooks", []):
            if hook.get("type", "command") != "command":
                raise ValueError("current settings contain a non-command hook")
            command = hook.get("command")
            if not isinstance(command, str) or not command.strip():
                raise ValueError("current settings contain an invalid hook command")
            if "hook-dispatch.py" in command:
                return None
            if "seat-guard.py" in command:
                continue
            hooks.append(hook)
    return {"entries": {event: {key: hooks}}}, hooks, path
