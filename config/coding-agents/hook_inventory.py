from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterator

from floor_allowlist import non_floor_check  # type: ignore[missing-import]

HOOK_TARGET = re.compile(r'(?:\$HOME|\$\{HOME\}|~|/)[^\s"\';|&)<>]*')


def command_paths(command: str, home: Path) -> Iterator[Path]:
    for word in HOOK_TARGET.findall(command):
        yield Path(re.sub(r"^(?:\$HOME|\$\{HOME\}|~)(?=/)", str(home), word))


def hook_targets(value: Any, home: Path) -> Iterator[Path]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "command" and isinstance(child, str):
                for path in command_paths(child, home):
                    if ".claude/hooks" in path.as_posix():
                        yield path
            else:
                yield from hook_targets(child, home)
    elif isinstance(value, list):
        for child in value:
            yield from hook_targets(child, home)


def keep_floor_targets(home: Path, changes: dict[Path, str | None], value: Any, check: Any = None) -> None:
    check = check or non_floor_check(home, changes)
    if isinstance(value, dict):
        command = value.get("command")
        if isinstance(command, str) and not check(command):
            for path in command_paths(command, home):
                if changes.get(path, "") is None:
                    del changes[path]
        for child in value.values():
            keep_floor_targets(home, changes, child, check)
    elif isinstance(value, list):
        for child in value:
            keep_floor_targets(home, changes, child, check)


def validate_hook_changes(home: Path, changes: dict[Path, str | None], settings: Any, kept: Any = ()) -> None:
    registries = home / ".claude/hooks/dispatch"
    documents = {home / ".claude/settings.json": settings}
    paths = set(registries.glob("registry*.json"))
    paths.update(path for path in changes if path.parent == registries and path.suffix == ".json")
    for path in paths:
        content = changes[path] if path in changes else path.read_text()
        if content is None:
            continue
        try:
            documents[path] = json.loads(content)
        except ValueError as exc:
            raise SystemExit(f"error: invalid hook registry {path}; nothing written") from exc
    problems = set()
    settings_path = home / ".claude/settings.json"
    for document, value in documents.items():
        for target in hook_targets(value, home):
            if target in changes and changes[target] is None:
                problems.add(f"{document.name} references hook scheduled for removal: {target}")
            elif document == settings_path and target not in changes and target not in kept and not target.is_file():
                problems.add(f"{document.name} references missing hook: {target}")
    if problems:
        raise SystemExit("error: unsafe hook installation; nothing written:\n" + "\n".join(sorted(problems)))


RELEASE_SCOPED = re.compile(r"factory-runtime|/factory/bin/|/repos/|/Volumes/")
HOOK_PATH = re.compile(r'(?:\$HOME|~|/)[^\s"\';|&)<>]*')


def hook_path_problems(home: Path, is_folded, read_registry, folded_rev, unfold_hooks) -> list[str]:

    settings_path = home / ".claude/settings.json"
    settings = json.loads(settings_path.read_text() if settings_path.exists() else "{}")
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    if is_folded(settings):
        registry = read_registry(home, folded_rev(settings))
        if registry is None:
            return ["the dispatcher registry for these settings is missing or damaged"]
        hooks = unfold_hooks(hooks, registry)
    problems = []
    for event, groups in hooks.items():
        for group in groups if isinstance(groups, list) else []:
            for hook in group.get("hooks", []) if isinstance(group, dict) else []:
                command = hook.get("command")
                if not isinstance(command, str):
                    continue
                for word in HOOK_PATH.findall(command):
                    if word in ("/", "~"):
                        continue
                    if RELEASE_SCOPED.search(word):
                        problems.append(f"{event}: release-scoped path {word} in: {command}")
                    elif (event == "PreToolUse" or ".claude/hooks/" in word) and not os.path.exists(
                        re.sub(r"^(?:\$HOME|~)(?=/)", str(home), word)
                    ):
                        problems.append(f"{event}: missing guard target {word} in: {command}")
    return problems
