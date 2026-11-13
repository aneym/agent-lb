from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

FOLD_EVENTS = ("PreToolUse", "PostToolUse")
SIMPLE = re.compile(r"[A-Za-z0-9_|]+")
HOOK_KEYS = {"type", "command", "timeout", "statusMessage"}
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 600
SCRIPT = "advisory-fold.py"
REGISTRY = "advisory-fold"
KEEP = 6


def covers_bash(matcher: Any) -> bool:
    if matcher in (None, "", "*"):
        return True
    return isinstance(matcher, str) and bool(SIMPLE.fullmatch(matcher)) and "Bash" in matcher.split("|")


def foldable(hook: Any, floor: re.Pattern[str]) -> bool:
    if not isinstance(hook, dict) or hook.get("type") != "command" or not set(hook) <= HOOK_KEYS:
        return False
    command, timeout = hook.get("command"), hook.get("timeout", DEFAULT_TIMEOUT)
    return (
        isinstance(command, str)
        and bool(command.strip())
        and not floor.search(command)
        and type(timeout) is int
        and 1 <= timeout <= MAX_TIMEOUT
    )


def split(groups: Any, floor: re.Pattern[str]) -> tuple[list[dict[str, Any]], list[Any]]:
    folded: list[dict[str, Any]] = []
    rest: list[Any] = []
    for group in groups if isinstance(groups, list) else []:
        hooks = group.get("hooks") if isinstance(group, dict) else None
        if not isinstance(hooks, list) or set(group) - {"matcher", "hooks"} or not covers_bash(group.get("matcher")):
            rest.append(group)
            continue
        left = []
        for hook in hooks:
            if foldable(hook, floor):
                entry = {"matcher": group.get("matcher"), "command": hook["command"]}
                entry["timeout"] = hook.get("timeout", DEFAULT_TIMEOUT)
                folded.append(entry)
            else:
                left.append(hook)
        if left:
            rest.append({**group, "hooks": left})
    return folded, rest


def fold(hooks: dict[str, Any], floor: re.Pattern[str]) -> tuple[dict[str, Any], dict[str, Any] | None, str | None]:
    plans = {event: split(hooks.get(event), floor) for event in FOLD_EVENTS}
    registry = {event: folded for event, (folded, _rest) in plans.items() if len(folded) > 1}
    if not registry:
        return hooks, None, None
    rev = hashlib.sha256(json.dumps(registry, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    out = dict(hooks)
    for event, folded in registry.items():
        timeout = min(MAX_TIMEOUT, sum(entry["timeout"] for entry in folded))
        command = f'/usr/bin/python3 "$HOME/.claude/hooks/{SCRIPT}" {event} {rev}'
        out[event] = [{"hooks": [{"type": "command", "command": command, "timeout": timeout}]}] + plans[event][1]
    return out, registry, rev


def place(path: Path, text: str, mode: int) -> None:
    if path.is_file() and path.read_text() == text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{os.getpid()}")
    staged.write_text(text)
    staged.chmod(mode)
    os.replace(staged, path)


def install(source: Path, home: Path, registry: dict[str, Any], rev: str) -> None:
    hooks = home / ".claude" / "hooks"
    place(hooks / SCRIPT, (source / "hooks" / SCRIPT).read_text(), 0o755)
    folder = hooks / REGISTRY
    place(folder / f"{rev}.json", json.dumps(registry, indent=2) + "\n", 0o644)
    old = sorted(folder.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in old[KEEP:]:
        if path.name != f"{rev}.json":
            path.unlink()
