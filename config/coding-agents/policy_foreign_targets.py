from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path


def tracked_paths(claude: Path) -> set[Path] | None:
    if not (claude / ".git").exists():
        return set()
    try:
        listed = subprocess.run(
            ["git", "-C", str(claude), "ls-files", "-z"], capture_output=True, check=True, timeout=30
        ).stdout.decode()
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        return None
    return {claude / name for name in listed.split("\0") if name}


def foreign_target(home: Path) -> Callable[[Path], bool]:
    claude = home / ".claude"
    tracked = tracked_paths(claude)

    def foreign(path: Path) -> bool:
        if path.is_symlink():
            return True
        if tracked is None:
            return path.is_relative_to(claude)
        return path in tracked

    return foreign
