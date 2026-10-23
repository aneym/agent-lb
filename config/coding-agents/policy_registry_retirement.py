from __future__ import annotations

import json
from pathlib import Path

from hook_inventory import hook_targets  # type: ignore[missing-import]
from policy_layout import RETIRED_HOOKS  # type: ignore[missing-import]


def without_retired_hooks(value, home, retired):
    if isinstance(value, list):
        return [
            without_retired_hooks(child, home, retired)
            for child in value
            if not (isinstance(child, dict) and "command" in child and retired.intersection(hook_targets(child, home)))
        ]
    if isinstance(value, dict):
        return {key: without_retired_hooks(child, home, retired) for key, child in value.items()}
    return value


def retire_hook_registries(home: Path, changes: dict[Path, str | None]) -> None:
    retired = {home / ".claude/hooks" / name for name in RETIRED_HOOKS}
    changes.update({path: None for path in retired if path.exists()})
    folder = home / ".claude/hooks/dispatch"
    paths = set(folder.glob("registry*.json"))
    paths.update(path for path in changes if path.parent == folder and path.name.startswith("registry"))
    for path in paths:
        if not path.is_file():
            continue
        changes.pop(path, None)
        registry = json.loads(path.read_text())
        cleaned = without_retired_hooks(registry, home, retired)
        if cleaned != registry:
            changes[path] = json.dumps(cleaned, indent=2, ensure_ascii=False) + "\n"
        for target in hook_targets(registry, home):
            if target not in retired and target in changes and changes[target] is None:
                changes.pop(target)
