import copy
import re
from parity_execution import hook_timeout


TOOL_ALIASES = {
    "Task": "Agent",
    "KillShell": "TaskStop",
    "KillBash": "TaskStop",
    "ListPeers": "ListAgents",
    "Brief": "SendUserMessage",
    "ListMcpResources": "ListMcpResourcesTool",
    "ReadMcpResource": "ReadMcpResourceTool",
    "ReadMcpResourceDir": "ReadMcpResourceDirTool",
}



NAME_LIST = re.compile(r"^[a-zA-Z0-9_|, -]+$")



TOOL_EVENTS = ("PreToolUse", "PostToolUse")



def matches(matcher, tool, event="PreToolUse"):
    if event not in TOOL_EVENTS or tool is None or not matcher or matcher == "*":
        return True
    if not isinstance(matcher, str):
        return False
    if NAME_LIST.match(matcher):
        names = [TOOL_ALIASES.get(name.strip(), name.strip()) for name in re.split(r"[|,]", matcher) if name.strip()]
        return tool in names
    try:
        pattern = re.compile(matcher)
    except re.error:
        return False
    return any(pattern.search(name) for name in [tool] + [a for a, t in TOOL_ALIASES.items() if t == tool])



def group_key(group):
    matcher = group.get("matcher")
    return "*" if matcher in (None, "", "*", ".*") else matcher



def old_hooks(per_hook, event, tool):
    seen, hooks = set(), []
    for group in per_hook.get(event, []):
        if not matches(group.get("matcher"), tool, event):
            continue
        for hook in group.get("hooks", []):
            if hook.get("command") in seen:
                continue
            seen.add(hook.get("command"))
            hooks.append(hook)
    return hooks



def new_hooks(dispatch, event, tool):
    seen, hooks = set(), []
    for group in dispatch.get(event, []):
        if not matches(group.get("matcher"), tool, event):
            continue
        for hook in group.get("hooks", []):
            if hook.get("command") not in seen:
                seen.add(hook.get("command"))
                hooks.append(hook)
    return hooks



def build_registry(per_hook, inproc):
    entries, dispatch = {}, {}
    for event, groups in per_hook.items():
        keys = []
        for group in groups:
            key = group_key(group)
            if key not in keys:
                keys.append(key)
            entries.setdefault(event, {}).setdefault(key, []).extend(copy.deepcopy(group["hooks"]))
        dispatch[event] = []
        for key in keys:
            hooks = entries[event][key]
            entry = {
                "type": "command",
                "command": "exec python3 \"$HOME/.claude/hooks/hook-dispatch.py\" %s '%s'" % (event, key),
                "timeout": int(sum(hook_timeout(h) for h in hooks) + 5),
            }
            group = {"hooks": [entry]}
            if key != "*":
                group = {"matcher": key, "hooks": [entry]}
            dispatch[event].append(group)
    return {"schema": 1, "per_hook": per_hook, "entries": entries, "dispatch": dispatch, "inproc": inproc}

