from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from policy_hook_groups import foldable_groups, is_dispatch_hook, keep_config_order, matcher_key, unfold_shared  # type: ignore[missing-import]  # fmt: skip
from policy_inventory import DISPATCH_INPROC  # type: ignore[missing-import]
from policy_layout import DISPATCH_EVENTS, DISPATCH_PYTHON, DISPATCH_REGISTRY, DISPATCH_REV, DISPATCH_SCRIPT  # type: ignore[missing-import]  # fmt: skip


def registry_problem(registry: Any) -> str | None:

    if not isinstance(registry, dict):
        return "not a registry"
    per_hook, entries, dispatch = registry.get("per_hook"), registry.get("entries"), registry.get("dispatch")
    if not (isinstance(per_hook, dict) and isinstance(entries, dict) and isinstance(dispatch, dict)):
        return "per_hook, entries or dispatch is missing"

    if registry.get("rev") and registry_rev(entries) != registry["rev"]:
        return f"entries do not hash to rev {registry['rev']}"

    for event in dict.fromkeys(list(entries) + list(per_hook) + list(dispatch)):
        keyed = entries.get(event)
        groups = per_hook.get(event)
        folded = dispatch.get(event)
        if not isinstance(keyed, dict) or not isinstance(groups, list) or not isinstance(folded, list):
            return f"{event}: entries, per_hook or dispatch is not the right shape"

        derived: dict[str, list[Any]] = {}
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                return f"{event}: a per_hook group is not a group"
            if group in folded:
                continue
            key = matcher_key(group)
            if key is None or key not in keyed:
                return f"{event}: per_hook group {group.get('matcher')!r} is neither kept nor folded"
            for hook in group["hooks"]:
                if not isinstance(hook, dict) or not isinstance(hook.get("command"), str):
                    return f"{event}: a per_hook hook is not a command hook"
                if all(hook["command"] != seen["command"] for seen in derived.setdefault(key, [])):
                    derived[key].append(hook)
        for key, hooks in keyed.items():
            if not isinstance(hooks, list) or not hooks:
                return f"{event} '{key}': the entry is not a list of hooks"
            if hooks != derived.get(key):
                return f"{event} '{key}': the entry is not the per_hook groups folded into it"
        for group in folded:
            if not isinstance(group, dict):
                return f"{event}: a folded group is not a group"
            for hook in group.get("hooks") or []:
                if is_dispatch_hook(hook):
                    match = re.search(r"hook-dispatch\.py\"?\s+(\w+)\s+'([^']*)'", hook["command"])
                    if not match or match.group(1) != event or match.group(2) not in keyed:
                        return f"{event}: folded entry {hook['command'][:80]!r} names no entry"
            if not any(is_dispatch_hook(hook) for hook in group.get("hooks") or []) and group not in groups:
                return f"{event}: folded list keeps a group per_hook does not have"
    return None


def registry_rev(entries: dict[str, Any]) -> str:

    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def dispatch_group(event: str, key: str, matcher_field: Any, hooks: list[dict[str, Any]], rev: str) -> dict[str, Any]:
    timeout = 15
    entry: dict[str, Any] = {
        "type": "command",
        "command": f'P={DISPATCH_PYTHON}; [ -x "$P" ] || P=python3; '
        f'exec "$P" -I -S "$HOME/.claude/{DISPATCH_SCRIPT}" {event} \'{key}\' {rev}',
        "timeout": int(timeout) if float(timeout).is_integer() else timeout,
    }
    messages = list(dict.fromkeys(hook["statusMessage"] for hook in hooks if hook.get("statusMessage")))
    if messages:
        entry["statusMessage"] = ", ".join(messages)
    group: dict[str, Any] = {"hooks": [entry]}
    if matcher_field is not None or key != "*":
        group = {"matcher": key if key != "*" else matcher_field, "hooks": [entry]}
    return group


def fold_hooks(
    hooks: dict[str, Any], only: dict[str, list[Any]] | None = None, worth: Any = None
) -> tuple[dict[str, Any], dict[str, Any] | None]:

    folded = json.loads(json.dumps(hooks))
    per_hook: dict[str, list[Any]] = {}
    entries: dict[str, dict[str, list[Any]]] = {}
    dispatch: dict[str, Any] = {}
    for event in DISPATCH_EVENTS:
        groups = hooks.get(event)
        if not isinstance(groups, list) or not groups:
            continue
        eligible = foldable_groups(groups, event)
        if only is not None:
            allowed = only.get(event, [])
            eligible = [good and group in allowed for group, good in zip(groups, eligible)]
        eligible = keep_config_order(groups, eligible, event)
        if worth is not None:
            by_key: dict[str, list[Any]] = {}
            for group, good in zip(groups, eligible):
                if good:
                    by_key.setdefault(matcher_key(group), []).extend(group["hooks"])
            unworthy = {key for key, key_hooks in by_key.items() if not worth(key_hooks)}
            eligible = [good and matcher_key(group) not in unworthy for group, good in zip(groups, eligible)]

            eligible = keep_config_order(groups, eligible, event)
        while True:
            settled = keep_config_order(groups, unfold_shared(groups, eligible), event)
            if settled == eligible:
                break
            eligible = settled
        if not any(eligible):
            continue
        out: list[Any] = []
        placed: dict[str, dict[str, Any]] = {}
        event_entries: dict[str, list[Any]] = {}
        for group, good in zip(groups, eligible):
            if not good:
                out.append(json.loads(json.dumps(group)))
                continue
            key = matcher_key(group)
            event_entries.setdefault(key, [])
            for hook in group["hooks"]:
                if all(hook["command"] != seen["command"] for seen in event_entries[key]):
                    event_entries[key].append(json.loads(json.dumps(hook)))
            if key not in placed:
                placed[key] = {"matcher_field": group.get("matcher"), "index": len(out)}
                out.append(None)
        per_hook[event] = json.loads(json.dumps(groups))
        entries[event] = event_entries
        dispatch[event] = (out, placed)
    if not entries:
        return folded, None
    rev = registry_rev(entries)
    for event, (out, placed) in list(dispatch.items()):
        for key, spot in placed.items():
            out[spot["index"]] = dispatch_group(event, key, spot["matcher_field"], entries[event][key], rev)
        dispatch[event] = out
        folded[event] = out
    registry = {
        "schema": 1,
        "rev": rev,
        "per_hook": per_hook,
        "dispatch": dispatch,
        "entries": entries,
        "inproc": list(DISPATCH_INPROC),
    }
    return folded, registry


def unfold_hooks(hooks: dict[str, Any], registry: dict[str, Any] | None) -> dict[str, Any]:

    unfolded = json.loads(json.dumps(hooks))
    for event in DISPATCH_EVENTS:
        groups = hooks.get(event)
        if not isinstance(groups, list) or not any(
            isinstance(group, dict) and any(is_dispatch_hook(hook) for hook in group.get("hooks") or [])
            for group in groups
        ):
            continue

        split: list[Any] = []
        for group in groups:
            members = group.get("hooks") if isinstance(group, dict) else None
            if (
                isinstance(members, list)
                and len(members) > 1
                and any(is_dispatch_hook(hook) for hook in members)
                and not all(is_dispatch_hook(hook) for hook in members)
            ):
                split.append({**group, "hooks": [hook for hook in members if is_dispatch_hook(hook)]})
                split.append({**group, "hooks": [hook for hook in members if not is_dispatch_hook(hook)]})
            else:
                split.append(group)
        groups = split
        if registry is None:
            raise ValueError(f"{event} hooks run through hook-dispatch.py but its registry is unreadable")
        if registry.get("dispatch", {}).get(event) == groups:
            unfolded[event] = json.loads(json.dumps(registry["per_hook"][event]))
            continue

        per_hook = registry.get("per_hook", {}).get(event, [])
        left = registry.get("dispatch", {}).get(event, [])
        plain = [
            group
            for group in groups
            if not (isinstance(group, dict) and any(is_dispatch_hook(hook) for hook in group.get("hooks") or []))
        ]
        keys = set()
        for group in groups:
            if group in plain:
                continue
            if len(group["hooks"]) != 1:
                raise ValueError(f"a {event} group mixes the dispatcher with other hooks; fix it by hand")
            match = re.search(r"hook-dispatch\.py\"?\s+(\w+)\s+'([^']*)'", group["hooks"][0]["command"])
            if not match or match.group(1) != event or match.group(2) not in registry.get("entries", {}).get(event, {}):
                raise ValueError(f"{event} dispatcher entry {group['hooks'][0]['command']!r} is not in the registry")
            keys.add(match.group(2))
        out: list[Any] = []
        for group in per_hook:
            if group in left:
                if group in plain:
                    plain.remove(group)
                    out.append(group)
            elif matcher_key(group) in keys:
                out.append(group)
        out.extend(plain)
        unfolded[event] = out
    return unfolded


def folded_revs(settings: dict[str, Any]) -> set[str | None]:

    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    revs: set[str | None] = set()
    for event in DISPATCH_EVENTS:
        for group in hooks.get(event) or []:
            for hook in (group.get("hooks") or []) if isinstance(group, dict) else []:
                if is_dispatch_hook(hook):
                    match = DISPATCH_REV.search(hook["command"])
                    revs.add(match.group(1) if match else None)
    return revs


def folded_rev(settings: dict[str, Any]) -> str | None:

    revs = folded_revs(settings)
    if len(revs) > 1:
        raise SystemExit(
            "error: the folded settings entries name different dispatcher registry revs (%s); no one registry can "
            "restore them, so nothing was written. Restore settings.json from the newest checkpoint under "
            "~/.agent-lb/config-checkpoints/coding-agents/ and run this again."
            % ", ".join(sorted(rev or "(none)" for rev in revs))
        )
    return next(iter(revs), None)


def read_registry(home: Path, rev: str | None = None) -> dict[str, Any] | None:

    base = home / DISPATCH_REGISTRY
    paths = [base, base.with_name("registry.json.bak")]
    if rev:
        paths.insert(0, base.with_name(f"registry.{rev}.json"))
    else:
        paths.append(base.with_name("registry.legacy.json"))
    for path in paths:
        try:
            registry = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if (registry.get("rev") if isinstance(registry, dict) else None) != rev:
            continue
        if registry_problem(registry) is None:
            return registry
        print(
            f"WARNING hook dispatcher: {path} cannot restore the per-hook config ({registry_problem(registry)}); "
            "trying the next copy"
        )
    return None


def is_folded(settings: dict[str, Any]) -> bool:
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    return any(
        is_dispatch_hook(hook)
        for event in DISPATCH_EVENTS
        for group in hooks.get(event) or []
        if isinstance(group, dict)
        for hook in group.get("hooks") or []
    )
