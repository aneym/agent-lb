from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

from floor_allowlist import floor_unwrap, non_floor_check  # type: ignore[missing-import]
from floor_hooks import accepted_forms, hooks_off, unclassified_guards  # type: ignore[missing-import]
from policy_root_hooks import held_by_root, root_floor_names  # type: ignore[missing-import]

HOOK_DOCUMENTS = {
    ".claude/settings.json": ".claude",
    ".claude/settings.local.json": ".claude",
    ".codex/hooks.json": ".codex",
}
REGISTRY_DIR = ".claude/hooks/dispatch"
NAME_LIST = re.compile(r"[A-Za-z0-9_]+(?:\|[A-Za-z0-9_]+)*")
DISPATCH_REV = re.compile(r"(hook-dispatch\.py\" \S+ '[^']*' )[0-9a-f]{12}")
SETTINGS_DOCUMENTS = (".claude/settings.json", ".claude/settings.local.json")


def managed_dir(home: Path, path: Path) -> Path | None:
    for name, owner in HOOK_DOCUMENTS.items():
        if path == home / name or os.path.realpath(path) == os.path.realpath(home / name):
            return home / owner
    if path.parent == home / REGISTRY_DIR and path.name.startswith("registry") and path.suffix == ".json":
        return home / REGISTRY_DIR
    return None


def outside(path: Path, home: Path, owner: Path) -> bool:
    real, root = os.path.realpath(path), os.path.join(os.path.realpath(home), os.path.relpath(owner, home))
    return os.path.commonpath([real, root]) != root


def entry_text(value: dict[str, Any], check: Any, live: bool) -> str:
    command = value["command"]
    if DISPATCH_REV.search(command):
        return json.dumps({"command": DISPATCH_REV.sub(r"\1<rev>", command)})
    inner = floor_unwrap(command, check) if live else None
    return json.dumps(dict(value, command=inner) if inner else value, sort_keys=True)


def floor_entries(value: Any, check: Any, live: bool, context: tuple[str, ...] = (), matcher: Any = None):
    if isinstance(value, dict):
        matcher = value.get("matcher", matcher)
        if isinstance(value.get("command"), str) and not check(value["command"]):
            yield "/".join(context), json.dumps(matcher), entry_text(value, check, live)
        for key, child in value.items():
            yield from floor_entries(child, check, live, context + (str(key),), matcher)
    elif isinstance(value, list):
        for child in value:
            yield from floor_entries(child, check, live, context, matcher)


def document_entries(text: str | None, label: str, check: Any, live: bool = False) -> list[tuple[str, str, str]]:
    if text is None or not text.strip():
        return []
    try:
        return list(floor_entries(json.loads(text), check, live))
    except ValueError:
        raise ValueError(f"{label} does not parse as JSON, so no hook entry in it can be checked") from None


def placed(entry: tuple[str, str, str]) -> tuple[str, str, str]:
    context, matcher, hook = entry
    parts = context.split("/")
    if len(parts) == 3 and parts[0] == "entries":
        return parts[1], json.dumps(parts[2]), hook
    if len(parts) == 3 and parts[0] in ("hooks", "per_hook") and parts[2] == "hooks":
        return parts[1], matcher, hook
    return context, matcher, hook


def overlaps(first: str, second: str) -> bool:
    names = [json.loads(matcher) for matcher in (first, second)]
    if any(not isinstance(name, str) or name in ("", "*") or not NAME_LIST.fullmatch(name) for name in names):
        return True
    return bool(set(names[0].split("|")) & set(names[1].split("|")))


def planned_text(path: Path, changes: dict[Path, str | None]) -> str | None:
    if path in changes:
        return changes[path]
    return path.read_text() if path.is_file() else None


def hooks_off_problems(home: Path, changes: dict[Path, str | None]) -> list[str]:
    problems = []
    for name in SETTINGS_DOCUMENTS:
        path = home / name
        for label, text in (
            ("live", path.read_text() if path.is_file() else None),
            ("planned", planned_text(path, changes)),
        ):
            try:
                found = hooks_off(json.loads(text)) if text and text.strip() else []
            except ValueError:
                continue
            problems += [f"{path}: {label} settings set {key}, which switches floor hooks off" for key in found]
    return list(dict.fromkeys(problems))


def floor_problems(home: Path, changes: dict[Path, str | None]) -> list[str]:
    problems, check = [], non_floor_check(home)
    registry, settings = home / REGISTRY_DIR / "registry.json", home / ".claude/settings.json"
    for path, content in changes.items():
        owner = managed_dir(home, path)
        if owner is None:
            continue
        if (path.is_symlink() or os.path.realpath(path) != str(path)) and outside(path, home, owner):
            problems.append(f"{path}: resolves outside {owner}")
            continue
        try:
            before = document_entries(path.read_text() if path.is_file() else None, f"live {path}", check, True)
            after = document_entries(content, f"planned {path}", check)
            if content is None and owner == home / REGISTRY_DIR:
                kept = Counter(
                    placed(entry)
                    for doc in (registry, settings)
                    if doc != path
                    for entry in document_entries(planned_text(doc, changes), f"planned {doc}", check)
                )
                gone = [entry for entry in before if not kept[placed(entry)]]
            else:
                gone = list((Counter(before) - Counter(after)).elements())
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            problems.append(f"{path}: {exc}")
            continue
        for context, matcher, hook in after:
            if floor_unwrap(json.loads(hook).get("command") or "", check):
                problems.append(f"{path}: floor entry at {context} matcher {matcher} keeps a fail-open wrapper: {hook}")
        held = root_floor_names(owner == home / ".codex")
        for context, matcher, hook in gone:
            if held_by_root(json.loads(hook).get("command") or "", held):
                continue
            problems.append(f"{path}: floor entry at {context} matcher {matcher} would change or go: {hook}")
        for context, matcher, hook in (Counter(after) - Counter(before)).elements():
            forms = accepted_forms(json.loads(hook).get("command"))
            if any(
                c == context and overlaps(m, matcher) and json.loads(old).get("command") in forms
                for c, m, old in before
            ):
                problems.append(
                    f"{path}: floor entry at {context} matcher {matcher} added beside a form already there: {hook}"
                )
    return problems


def commands(value: Any):
    if isinstance(value, dict):
        if isinstance(value.get("command"), str):
            yield value["command"]
        for child in value.values():
            yield from commands(child)
    elif isinstance(value, list):
        for child in value:
            yield from commands(child)


def guard_problems(home: Path, changes: dict[Path, str | None]) -> list[str]:
    paths = {home / name for name in HOOK_DOCUMENTS} | set((home / REGISTRY_DIR).glob("registry*.json"))
    paths |= {path for path in changes if managed_dir(home, path) is not None}
    problems = []
    for path in sorted(paths):
        for text in (path.read_text() if path.is_file() else None, changes.get(path)):
            try:
                value = json.loads(text) if text else None
            except ValueError:
                continue
            for command in commands(value):
                for name in sorted(unclassified_guards(command)):
                    problems.append(f"{path}: hook script {name} is a guard with no floor classification: {command}")
    return list(dict.fromkeys(problems))


def refuse_floor_changes(home: Path, changes: dict[Path, str | None]) -> None:
    problems = floor_problems(home, changes) + guard_problems(home, changes) + hooks_off_problems(home, changes)
    if problems:
        for line in problems:
            print(f"floor invariant: {line}")
        raise SystemExit(
            f"error: floor invariant refused the install ({len(problems)} entries); settings, Codex hooks and registries unchanged"
        )
