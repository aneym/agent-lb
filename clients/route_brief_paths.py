from __future__ import annotations

import re
from pathlib import Path

SHARED_ROOTS = frozenset(
    {"bin", "dev", "etc", "lib", "lib64", "opt", "proc", "sbin", "sys", "usr", "System", "Library", "Applications"}
)
SYNCED_HOME = tuple(Path.home() / name for name in (".claude", ".agents", ".agent-lb", ".codex", ".local/bin"))
PATH_TOKEN = re.compile(r"(?<![\w/~.:-])(~?/[^\s`'\"<>()\[\]{},;|]+)")


def caller_host_paths(brief: str, root: Path) -> list[str]:
    found: list[str] = []
    for token in PATH_TOKEN.findall(brief):
        text = token.rstrip(".:")
        path = Path(text).expanduser()
        if len(path.parts) < 2 or path.parts[1] in SHARED_ROOTS:
            continue
        if any(path == home or home in path.parents for home in SYNCED_HOME):
            continue
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if resolved == root or root in resolved.parents:
            continue
        if text not in found:
            found.append(text)
    return found
