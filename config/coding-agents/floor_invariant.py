from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from floor_hooks import FLOOR_MENTIONS, accepted_forms, mentioned_floors  # type: ignore[missing-import]
from policy_layout import SETTINGS_HOOKS  # type: ignore[missing-import]

HOOK_DOCUMENTS = (".claude/settings.json", ".claude/settings.local.json", ".codex/hooks.json")
REGISTRY_DIR = ".claude/hooks/dispatch"


def hook_document(home: Path, path: Path) -> bool:
    real = os.path.realpath(path)
    if real in {os.path.realpath(home / name) for name in HOOK_DOCUMENTS}:
        return True
    return (
        os.path.dirname(real) == os.path.realpath(home / REGISTRY_DIR)
        and path.name.startswith("registry")
        and path.suffix == ".json"
    )


def floor_entries(value: Any, context: tuple[str, ...] = (), matcher: Any = None):
    if isinstance(value, dict):
        matcher = value.get("matcher", matcher)
        if mentioned_floors(value.get("command")):
            yield "/".join(context), json.dumps(matcher), json.dumps(value, sort_keys=True)
        for key, child in value.items():
            yield from floor_entries(child, context + (str(key),), matcher)
    elif isinstance(value, list):
        for child in value:
            yield from floor_entries(child, context, matcher)


def document_entries(text: str | None, label: str) -> list[tuple[str, str, str]]:
    if text is None or not text.strip():
        return []
    try:
        return list(floor_entries(json.loads(text)))
    except ValueError:
        if any(term in text for term in FLOOR_MENTIONS):
            raise ValueError(f"{label} does not parse as JSON and mentions a floor hook")
        return []


def planned_text(path: Path, changes: dict[Path, str | None]) -> str | None:
    if path in changes:
        return changes[path]
    return path.read_text() if path.is_file() else None


def floor_problems(home: Path, changes: dict[Path, str | None], removable: frozenset[str]) -> list[str]:
    problems = []
    active = home / REGISTRY_DIR / "registry.json"
    for path, content in changes.items():
        if not hook_document(home, path):
            continue
        try:
            before = document_entries(path.read_text() if path.is_file() else None, f"live {path}")
            if content is None and path.name != active.name and path.parent == active.parent:
                after = document_entries(planned_text(active, changes), f"planned {active}")
            else:
                after = document_entries(content, f"planned {path}")
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            problems.append(f"{path}: {exc}")
            continue
        for context, matcher, hook in (Counter(before) - Counter(after)).elements():
            if json.loads(hook).get("command") not in removable:
                problems.append(f"{path}: floor entry at {context} matcher {matcher} would change or go: {hook}")
        for context, matcher, hook in (Counter(after) - Counter(before)).elements():
            forms = accepted_forms(json.loads(hook).get("command"))
            if any((c, m) == (context, matcher) and json.loads(old).get("command") in forms for c, m, old in before):
                problems.append(
                    f"{path}: floor entry at {context} matcher {matcher} added beside a form already there: {hook}"
                )
    return problems


def refuse_floor_changes(home: Path, changes: dict[Path, str | None], uninstall: bool) -> None:
    managed = {form for _event, _matcher, hook, _source in SETTINGS_HOOKS for form in accepted_forms(hook["command"])}
    problems = floor_problems(home, changes, frozenset(managed if uninstall else ()))
    if problems:
        for line in problems:
            print(f"floor invariant: {line}")
        raise SystemExit(
            f"error: floor invariant refused the install ({len(problems)} entries); settings, Codex hooks and registries unchanged"
        )
