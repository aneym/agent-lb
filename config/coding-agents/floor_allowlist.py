from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from pathlib import Path

from claude_config_hooks import manifest_root  # type: ignore[missing-import]
from codex_home import OFFLOAD_COMMAND  # type: ignore[missing-import]
from floor_hooks import FLOOR_STEMS, digest, non_floor, script_path, stem, unwrapped  # type: ignore[missing-import]
from policy_layout import SETTINGS_HOOKS  # type: ignore[missing-import]

SOURCE_HOOKS = Path(__file__).resolve().parent / "hooks"


def shipped_files(home: Path) -> list[tuple[Path, list[Path]]]:
    pairs = [(path, [path, home / ".claude/hooks" / path.name]) for path in sorted(SOURCE_HOOKS.iterdir())]
    root = manifest_root(home)
    if root is not None and (root / "hooks").is_dir():
        pairs += [(path, [path, home / ".claude/hooks" / path.name]) for path in sorted((root / "hooks").iterdir())]
    analytics = home / "repos/skill-stats/dist"
    if analytics.is_dir():
        for path in sorted(analytics.rglob("*")):
            pairs.append((path, [path, home / ".local/share/skill-stats/dist" / path.relative_to(analytics)]))
    return pairs


def allowed_scripts(home: Path, changes: dict[Path, str | None] | None = None) -> dict[str, set[str]]:
    planned = {os.path.realpath(path): content for path, content in (changes or {}).items()}
    scripts: dict[str, set[str]] = {}
    for source, targets in shipped_files(home):
        if not source.is_file() or stem(source.name) in FLOOR_STEMS:
            continue
        value = digest(str(source))
        for target in targets:
            real = os.path.realpath(target)
            found = scripts.setdefault(real, set())
            if value:
                found.add(value)
            content = planned.get(real)
            if content is not None:
                found.add(hashlib.sha256(content.encode()).hexdigest())
    return scripts


def exact_commands() -> frozenset[str]:
    return frozenset([hook["command"] for _event, _matcher, hook, _source in SETTINGS_HOOKS] + [OFFLOAD_COMMAND])


def non_floor_check(home: Path | None, changes: dict[Path, str | None] | None = None) -> Callable[[object], bool]:
    if home is None:
        return lambda _command: False
    scripts, exact = allowed_scripts(home, changes), exact_commands()
    return lambda command: non_floor(command, scripts, exact)


def floor_unwrap(command: str, check: Callable[[object], bool]) -> str | None:
    inner = unwrapped(command)
    return inner if inner is not None and not check(inner) and script_path(inner) else None


def unwrap_floors(value: object, check: Callable[[object], bool]) -> object:
    if isinstance(value, list):
        return [unwrap_floors(item, check) for item in value]
    if not isinstance(value, dict):
        return value
    copy = {key: unwrap_floors(item, check) for key, item in value.items()}
    command = copy.get("command")
    inner = floor_unwrap(command, check) if isinstance(command, str) else None
    if inner:
        copy["command"] = inner
    return copy
