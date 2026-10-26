from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path
from typing import Any

from policy_hook_groups import dispatcher_module  # type: ignore[missing-import]
from policy_layout import DISPATCH_HANDLERS, DISPATCH_HOOK_KEYS  # type: ignore[missing-import]

EVENT_NAME = re.compile(r"[A-Z][A-Za-z]+")
INTERPRETER = re.compile(r"(?:python[0-9.]*|bash|sh|zsh|node|bun|env|exec)")
HOME_WORD = re.compile(r"^(?:\$HOME|\$\{HOME\}|~)(?=/)")


def program_of(command: str, home: Path) -> str | None:
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    for word in words:
        if word.startswith("-") or "=" in word.split("/")[0] or INTERPRETER.fullmatch(os.path.basename(word)):
            continue
        return os.path.normpath(HOME_WORD.sub(str(home), word))
    return None


def any_matcher(matcher: Any) -> bool:
    return matcher in (None, "", "*")


def same_matcher(first: Any, second: Any) -> bool:
    return (any_matcher(first) and any_matcher(second)) or first == second


def handler_problem(entry: Any, home: Path, source: Path) -> str | None:
    if not isinstance(entry, dict) or set(entry) - {"event", "matcher", "hook"}:
        return "an entry is not {event, matcher, hook}"
    event, matcher, hook = entry.get("event"), entry.get("matcher"), entry.get("hook")
    if not isinstance(event, str) or not EVENT_NAME.fullmatch(event):
        return f"event {event!r} is not a hook event"
    if matcher is not None and not isinstance(matcher, str):
        return f"{event} matcher is not a string"
    if not isinstance(hook, dict) or set(hook) - DISPATCH_HOOK_KEYS - {"async"} or hook.get("type") != "command":
        return f"{event} hook is not a command hook"
    command, timeout = hook.get("command"), hook.get("timeout")
    if not isinstance(command, str) or not command.strip():
        return f"{event} hook has no command"
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0):
        return f"{event} hook timeout is not a positive number"
    if dispatcher_module(source).floor_name(command):
        return f"{event} hook names a floor guard, which only agent-lb registers"
    program = program_of(command, home)
    if not program or not program.startswith("/"):
        return f"{event} hook command does not start with its program path"
    return None


def load_handlers(home: Path, source: Path) -> list[tuple[str, Any, dict[str, Any]]]:
    handlers = []
    for path in sorted((home / DISPATCH_HANDLERS).glob("*.json")):
        try:
            entries = json.loads(path.read_text()).get("hooks")
        except (OSError, ValueError, AttributeError) as exc:
            print(f"WARNING hook handlers: {path} ignored ({exc.__class__.__name__})")
            continue
        if not isinstance(entries, list):
            problems = ["no hooks list"]
        else:
            problems = [problem for problem in (handler_problem(entry, home, source) for entry in entries) if problem]
        if problems:
            print(f"WARNING hook handlers: {path} ignored ({problems[0]})")
            continue
        handlers.extend((entry["event"], entry.get("matcher"), dict(entry["hook"])) for entry in entries)
    return handlers


def apply_handlers(
    settings: dict[str, Any], handlers: list[tuple[str, Any, dict[str, Any]]], home: Path
) -> dict[str, list[Any]]:
    hooks = settings.setdefault("hooks", {})
    placed: dict[str, list[Any]] = {}
    for event, matcher, hook in handlers:
        program = program_of(hook["command"], home)
        group: dict[str, Any] = {"hooks": [dict(hook)]}
        if not any_matcher(matcher):
            group = {"matcher": matcher, **group}
        kept: list[Any] = []
        spot = None
        for existing in hooks.get(event) or []:
            members = existing.get("hooks") if isinstance(existing, dict) else None
            if isinstance(members, list) and same_matcher(existing.get("matcher"), matcher):
                left = [
                    member
                    for member in members
                    if not (
                        isinstance(member, dict)
                        and isinstance(member.get("command"), str)
                        and program_of(member["command"], home) == program
                    )
                ]
                if len(left) != len(members):
                    spot = len(kept) if spot is None else spot
                    if left:
                        kept.append({**existing, "hooks": left})
                    continue
            kept.append(existing)
        kept.insert(len(kept) if spot is None else spot, group)
        hooks[event] = kept
        placed.setdefault(event, []).append(json.loads(json.dumps(group)))
    return placed


def handler_folds_known(candidate: dict[str, Any], old: dict[str, Any], placed: dict[str, list[Any]]) -> bool:
    def commands(registry: dict[str, Any], event: str) -> set[str]:
        return {hook["command"] for hooks in (registry.get("entries") or {}).get(event, {}).values() for hook in hooks}

    for event, groups in placed.items():
        folded, known = commands(candidate, event), commands(old, event)
        for group in groups:
            if any(hook["command"] in folded and hook["command"] not in known for hook in group["hooks"]):
                return False
    return True
