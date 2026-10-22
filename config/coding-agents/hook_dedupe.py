from typing import Any


def dedupe_hooks(hooks: Any) -> None:

    if not isinstance(hooks, dict):
        return
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        seen: set[tuple[Any, str]] = set()
        kept = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                kept.append(group)
                continue
            items = []
            for hook in group["hooks"]:
                command = hook.get("command") if isinstance(hook, dict) else None
                key = (group.get("matcher"), command)
                if isinstance(command, str) and key in seen:
                    continue
                if isinstance(command, str):
                    seen.add(key)
                items.append(hook)
            if items:
                kept.append({**group, "hooks": items})
        hooks[event] = kept
