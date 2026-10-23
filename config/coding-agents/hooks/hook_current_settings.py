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
    if "hook-dispatch.py" in json.dumps(groups):
        return None
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
            if "seat-guard.py" in command:
                raise ValueError("current settings still register the cached seat guard")
            hooks.append(hook)
    return {"entries": {event: {key: hooks}}}, hooks, path
