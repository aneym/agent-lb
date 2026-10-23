from __future__ import annotations

import json
from pathlib import Path

from hook_inventory import hook_targets  # type: ignore[missing-import]


def retire_hook_registries(home: Path, changes: dict[Path, str | None]) -> None:
    folder = home / ".claude/hooks/dispatch"
    paths = set(folder.glob("registry*.json"))
    paths.update(path for path in changes if path.parent == folder and path.name.startswith("registry"))
    for path in paths:
        content = changes[path] if path in changes else path.read_text()
        if content is None:
            continue
        registry = json.loads(content)
        if any(target in changes and changes[target] is None for target in hook_targets(registry, home)):
            changes[path] = None
