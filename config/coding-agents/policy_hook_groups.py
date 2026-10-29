from __future__ import annotations  # noqa: I001

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import hookd_install  # type: ignore[missing-import]
from floor_hooks import FLOOR_HOOKS, accepted_forms  # type: ignore[missing-import]
from hook_retired_invocation import retired_invocation  # type: ignore[missing-import]
from policy_layout import DISPATCH_DROPPED, DISPATCH_HOOK_KEYS, DISPATCH_SCRIPT, DISPATCH_SKIP_MATCHERS, HOOK_MANIFEST, NAME_LIST, RETIRED_EVENT_HOOKS, RETIRED_HOOKS, SIMPLE_MATCHER, TOOL_ALIASES, UNREGISTERED_HOOKS  # type: ignore[missing-import]  # noqa: I001  # fmt: skip


def installed_manifest(source: Path) -> str:

    module = dispatcher_module(source)
    doc = json.loads((source / HOOK_MANIFEST).read_text())
    for entry in doc["hooks"]:
        found = re.search(r'hooks/([^"\s]+)', entry["hook"]["command"])
        name = found.group(1) if found else ""
        script = source / "hooks" / name
        prefilter = module.SCRIPT_PREFILTERS.get(name)
        entry["floor"] = name in FLOOR_HOOKS
        entry["prefilter"] = prefilter[1].__name__ if prefilter else None
        entry["sha256"] = hashlib.sha256(script.read_bytes()).hexdigest() if script.is_file() else None
    return json.dumps(doc, indent=2) + "\n"


def is_settings_hook(command: Any, hook: dict[str, Any]) -> bool:
    return isinstance(command, str) and command in accepted_forms(hook["command"])


def dispatcher_module(source: Path) -> Any:

    cached = getattr(dispatcher_module, "loaded", {})
    if source not in cached:
        import importlib.util

        spec = importlib.util.spec_from_file_location("hook_dispatch_source", source / "hooks" / "hook-dispatch.py")
        if spec is None or spec.loader is None:
            raise SystemExit(f"error: cannot load {source / 'hooks' / 'hook-dispatch.py'}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cached[source] = module
        dispatcher_module.loaded = cached  # type: ignore[attr-defined]
    return cached[source]


def opaque_floor_commands(hooks: dict[str, Any], source: Path) -> list[str]:

    module = dispatcher_module(source)
    found = []
    for group in hooks.get("PreToolUse") or []:
        for hook in (group.get("hooks") or []) if isinstance(group, dict) else []:
            command = hook.get("command") if isinstance(hook, dict) else None
            name = module.floor_name(command) if isinstance(command, str) else None
            if name and module.floor_exec(command, name) is None:
                found.append(f"{group.get('matcher') or '*'}: {command[:120]!r} (floor guard {name})")
    return found


DISPATCH_COMMAND = re.compile(
    "|".join(
        re.escape(template)
        .replace(re.escape("{script}"), re.escape(DISPATCH_SCRIPT))
        .replace(re.escape("{event}"), r"\w+")
        .replace(re.escape("{key}"), r"[^']*")
        .replace(re.escape("{rev}"), r"[0-9a-f]{12}")
        for template in (hookd_install.PLAIN_COMMAND, hookd_install.CLIENT_COMMAND)
    )
)


def is_dispatch_hook(hook: Any) -> bool:
    return (
        isinstance(hook, dict)
        and isinstance(hook.get("command"), str)
        and bool(DISPATCH_COMMAND.fullmatch(hook["command"]))
    )


def matcher_key(group: dict[str, Any]) -> str | None:

    matcher = group.get("matcher")
    if matcher in (None, "", "*"):
        return "*"
    if isinstance(matcher, str) and SIMPLE_MATCHER.fullmatch(matcher):
        return matcher
    return None


def tool_names(key: str) -> set[str]:
    return {TOOL_ALIASES.get(name.strip(), name.strip()) for name in re.split(r"[|,]", key) if name.strip()}


def keys_overlap(first: str, second: str) -> bool:
    return first == "*" or second == "*" or bool(tool_names(first) & tool_names(second))


ORDER_FREE_EVENTS = {"PostToolUse"}


def foldable_groups(groups: list[Any], event: str) -> list[bool]:

    ok = []
    for group in groups:
        key = matcher_key(group) if isinstance(group, dict) else None
        hooks = group.get("hooks") if isinstance(group, dict) else None
        good = (
            key is not None
            and key not in DISPATCH_SKIP_MATCHERS
            and isinstance(hooks, list)
            and bool(hooks)
            and set(group) <= {"matcher", "hooks"}
            and all(
                isinstance(hook, dict)
                and hook.get("type") == "command"
                and set(hook) <= DISPATCH_HOOK_KEYS
                and isinstance(hook.get("command"), str)
                and hook["command"].strip()
                and not is_dispatch_hook(hook)
                and (
                    hook.get("timeout") is None
                    or (
                        isinstance(hook["timeout"], (int, float))
                        and not isinstance(hook["timeout"], bool)
                        and hook["timeout"] > 0
                    )
                )
                and (hook.get("statusMessage") is None or isinstance(hook["statusMessage"], str))
                for hook in hooks
            )
        )
        ok.append(good)
    return unfold_shared(groups, ok)


def commands_of(group: Any) -> set[str]:
    hooks = group.get("hooks") if isinstance(group, dict) else None
    return (
        {hook["command"] for hook in hooks if isinstance(hook, dict) and isinstance(hook.get("command"), str)}
        if isinstance(hooks, list)
        else set()
    )


def unfold_shared(groups: list[Any], eligible: list[bool]) -> list[bool]:

    eligible = list(eligible)
    changed = True
    while changed:
        changed = False
        for index, (group, good) in enumerate(zip(groups, eligible)):
            if not good:
                continue
            key, mine = matcher_key(group), commands_of(group)
            for other_index, other in enumerate(groups):
                if other_index == index or not mine & commands_of(other):
                    continue
                if eligible[other_index]:
                    clash = matcher_key(other) != key and keys_overlap(key, matcher_key(other))
                else:
                    clash = group_overlaps(other, key)
                if clash:
                    eligible[index] = False
                    changed = True
                    break
    return eligible


def group_overlaps(group: Any, key: str) -> bool:

    if not isinstance(group, dict):
        return True
    own = matcher_key(group)
    if own is not None:
        return keys_overlap(own, key)
    if key == "*" or not isinstance(group.get("matcher"), str):
        return True
    if NAME_LIST.fullmatch(group["matcher"]):
        return keys_overlap(group["matcher"], key)
    try:
        pattern = re.compile(group["matcher"])
    except re.error:
        return True
    names = tool_names(key)
    names |= {alias for alias, tool in TOOL_ALIASES.items() if tool in names}
    return any(pattern.search(name) for name in names)


def keep_config_order(groups: list[Any], eligible: list[bool], event: str) -> list[bool]:

    eligible = list(eligible)
    if event in ORDER_FREE_EVENTS:
        return eligible
    changed = True
    while changed:
        changed = False
        first: dict[str, int] = {}
        for index, (group, good) in enumerate(zip(groups, eligible)):
            if not good:
                continue
            key = matcher_key(group)
            if key not in first:
                first[key] = index
                continue
            if any(
                group_overlaps(groups[between], key) and not (eligible[between] and matcher_key(groups[between]) == key)
                for between in range(first[key] + 1, index)
            ):
                eligible[index] = False
                changed = True
    return eligible


def drop_hooks(
    hooks: dict[str, Any], retired_kept: frozenset[str] = frozenset()
) -> tuple[dict[str, Any], list[str], list[str]]:

    kept, dropped, refused = {}, [], []
    names = tuple(n for n in (*RETIRED_HOOKS, *UNREGISTERED_HOOKS) if n not in retired_kept)
    for event, groups in hooks.items():
        kept[event] = groups
        if not isinstance(groups, list):
            continue
        out = []
        retired = names + RETIRED_EVENT_HOOKS.get(event, ())
        for group in groups:
            if not (isinstance(group, dict) and isinstance(group.get("hooks"), list)):
                out.append(group)
                continue
            left = []
            for hook in group["hooks"]:
                command = hook.get("command") if isinstance(hook, dict) else None
                name = None
                if isinstance(command, str):
                    name = DISPATCH_DROPPED.get(hashlib.sha256(command.encode()).hexdigest())
                    if not name:
                        mentioned = next((n for n in retired if n in command), None)
                        if mentioned and retired_invocation(command, retired):
                            name = mentioned
                        elif mentioned:
                            refused.append(
                                f"{event} {group.get('matcher') or '*'}: {mentioned} in ~/.claude/settings.json: "
                                f"not a single retired invocation, left untouched: {command}"
                            )
                if name:
                    dropped.append(f"{event} {group.get('matcher') or '*'}: {name}")
                else:
                    left.append(hook)
            if left:
                out.append({**group, "hooks": left})
            elif not group["hooks"]:
                out.append(group)
        kept[event] = out
    return kept, dropped, refused


def settled_matcher(event: str, matcher: Any, command: Any) -> Any:
    if (
        event == "PostToolUse"
        and isinstance(command, str)
        and "idle-agents.py" in command
        and isinstance(matcher, str)
        and matcher != "*"
        and "TaskStop" not in matcher.split("|")
    ):
        return matcher + "|TaskStop"
    return matcher
