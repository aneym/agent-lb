from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from floor_hooks import accepted_forms  # type: ignore[missing-import]
from policy_hook_groups import drop_hooks, settled_matcher  # type: ignore[missing-import]
from policy_inventory import MANAGED_HOOKS  # type: ignore[missing-import]
from policy_layout import SETTINGS_HOOKS  # type: ignore[missing-import]

SOURCES = (Path(".claude"), Path(".cache/claude-config-hooks"))
STATE = Path(".claude/hooks/.claude-config-hooks.json")


def manifest_root(home: Path) -> Path | None:
    for relative in SOURCES:
        try:
            manifest = json.loads((home / relative / "hooks.json").read_text())
        except (OSError, ValueError):
            continue
        if (
            isinstance(manifest, dict)
            and isinstance(manifest.get("hooks"), list)
            and (home / relative / "hooks").is_dir()
        ):
            return home / relative
    return None


def agent_lb_commands() -> set[str]:
    return {form for _event, _matcher, hook, _source in SETTINGS_HOOKS for form in accepted_forms(hook["command"])}


def desired_hooks(home: Path, root: Path, manifest: dict[str, Any]) -> dict[tuple[Any, Any, str], dict[str, Any]]:
    platform = "darwin" if sys.platform == "darwin" else "linux"
    hooks_dir, ours = home / ".claude/hooks", agent_lb_commands()
    desired = {}
    for entry in manifest["hooks"]:
        if platform not in entry["os"] or entry["command"] in ours:
            continue
        if drop_hooks({entry["event"]: [{"hooks": [{"command": entry["command"]}]}]})[1]:
            continue
        absent = [
            name
            for name in entry.get("files", [])
            if Path(name).name not in MANAGED_HOOKS
            and not (root / name).exists()
            and not (hooks_dir / Path(name).name).exists()
        ]
        if absent:
            print(f"claude-config hook {entry['id']} skipped: {', '.join(absent)} missing")
            continue
        matcher = settled_matcher(entry["event"], entry.get("matcher"), entry["command"])
        desired[(entry["event"], matcher, entry["command"])] = entry
    return desired


def claude_config_hooks(home: Path, hooks: dict[str, Any]) -> None:
    root = manifest_root(home)
    if root is None:
        return
    manifest = json.loads((root / "hooks.json").read_text())
    desired = desired_hooks(home, root, manifest)
    try:
        state = json.loads((home / STATE).read_text())
    except (OSError, ValueError):
        state = {"owned": []}
    retired = [command for command in manifest.get("retired_commands", []) if isinstance(command, str)]
    owned = set(state.get("owned", [])) | {entry["command"] for entry in manifest["hooks"]} | set(retired)
    owned -= agent_lb_commands()
    seen = set()
    for event, groups in list(hooks.items()):
        kept_groups = []
        for group in groups:
            items = []
            for item in group.get("hooks", []):
                command = item.get("command")
                triple = (event, group.get("matcher"), command)
                if command not in owned:
                    items.append(item)
                    continue
                if triple not in desired or triple in seen:
                    print(f"claude-config hook removed: {command[:80]} ({event})")
                    continue
                seen.add(triple)
                updated = dict(item)
                if "timeout" in desired[triple]:
                    updated["timeout"] = desired[triple]["timeout"]
                else:
                    updated.pop("timeout", None)
                items.append(updated)
            if items:
                kept_groups.append({**group, "hooks": items})
        if kept_groups:
            hooks[event] = kept_groups
        else:
            hooks.pop(event)
    for (event, matcher, command), entry in desired.items():
        if (event, matcher, command) in seen:
            continue
        item = {"type": "command", "command": command}
        if "timeout" in entry:
            item["timeout"] = entry["timeout"]
        group: dict[str, Any] = {} if matcher is None else {"matcher": matcher}
        group["hooks"] = [item]
        hooks.setdefault(event, []).append(group)
        print(f"claude-config hook added: {entry['id']} ({event})")


def claude_config_files(home: Path) -> dict[Path, str | None]:
    root = manifest_root(home)
    if root is None:
        return {}
    manifest = json.loads((root / "hooks.json").read_text())
    desired = desired_hooks(home, root, manifest)
    hooks_dir, changes = home / ".claude/hooks", {}
    for entry in desired.values():
        for name in entry.get("files", []):
            source, target = root / name, hooks_dir / Path(name).name
            if (
                target.name in MANAGED_HOOKS
                or not source.is_file()
                or os.path.realpath(source) == os.path.realpath(target)
            ):
                continue
            try:
                text = source.read_text()
            except UnicodeDecodeError:
                print(f"claude-config hook file skipped (not UTF-8): {source}")
                continue
            if not target.is_file() or target.read_text() != text:
                changes[target] = text
    state = json.dumps({"owned": list(dict.fromkeys(command for _e, _m, command in desired))}, indent=2) + "\n"
    if (home / STATE).is_file() and (home / STATE).read_text() == state:
        return changes
    return {**changes, home / STATE: state}
