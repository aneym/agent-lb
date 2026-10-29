from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from claude_config_hooks import manifest_root  # type: ignore[missing-import]
from codex_home import OFFLOAD_COMMAND  # type: ignore[missing-import]
from floor_hooks import FLOOR_STEMS, MAX_DIGEST_BYTES, non_floor, stem, unwrapped  # type: ignore[missing-import]
from policy_layout import SETTINGS_HOOKS  # type: ignore[missing-import]

SOURCE_HOOKS = Path(__file__).resolve().parent / "hooks"


def file_bytes(path: Path) -> bytes | None:
    try:
        if not path.is_file() or path.stat().st_size > MAX_DIGEST_BYTES:
            return None
        return path.read_bytes()
    except OSError:
        return None


def committed_reader(root: Path) -> Callable[[str], bytes | None]:
    def read(name: str) -> bytes | None:
        try:
            top = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=20
            )
            if top.returncode or os.path.realpath(top.stdout.strip()) != os.path.realpath(root):
                return None
            shown = subprocess.run(["git", "-C", str(root), "show", f"HEAD:{name}"], capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return None
        return shown.stdout if shown.returncode == 0 else None

    return read


def shipped_manifest(home: Path) -> tuple[list[dict[str, Any]], Callable[[str], bytes | None]]:
    root = manifest_root(home)
    if root is None:
        return [], lambda _name: None
    live = os.path.realpath(root) == os.path.realpath(home / ".claude")
    read = committed_reader(root) if live else lambda name: file_bytes(root / name)
    try:
        manifest = json.loads(read("hooks.json") or b"")
    except ValueError:
        return [], read
    entries = manifest.get("hooks") if isinstance(manifest, dict) else None
    return [entry for entry in entries or [] if isinstance(entry, dict)], read


def shipped_files(home: Path) -> Iterator[tuple[str, bytes | None, list[Path]]]:
    for path in sorted(SOURCE_HOOKS.iterdir()):
        yield path.name, file_bytes(path), [path, home / ".claude/hooks" / path.name]
    entries, read = shipped_manifest(home)
    names = {name for entry in entries for name in entry.get("files") or [] if isinstance(name, str)}
    for name in sorted(names):
        yield Path(name).name, read(name), [home / ".claude/hooks" / Path(name).name]
    analytics = home / "repos/skill-stats/dist"
    if analytics.is_dir():
        for path in sorted(analytics.rglob("*")):
            target = home / ".local/share/skill-stats/dist" / path.relative_to(analytics)
            yield path.name, file_bytes(path), [path, target]


def allowed_scripts(home: Path) -> dict[str, set[str]]:
    scripts: dict[str, set[str]] = {}
    for name, data, targets in shipped_files(home):
        if data is None or stem(name) in FLOOR_STEMS:
            continue
        value = hashlib.sha256(data).hexdigest()
        for target in targets:
            scripts.setdefault(os.path.realpath(target), set()).add(value)
    return scripts


def exact_commands(home: Path) -> frozenset[str]:
    shipped = [entry.get("command") for entry in shipped_manifest(home)[0]]
    return frozenset(
        [hook["command"] for _event, _matcher, hook, _source in SETTINGS_HOOKS]
        + [OFFLOAD_COMMAND]
        + [command for command in shipped if isinstance(command, str)]
    )


def non_floor_check(home: Path | None) -> Callable[[object], bool]:
    if home is None:
        return lambda _command: False
    scripts, exact = allowed_scripts(home), exact_commands(home)
    return lambda command: non_floor(command, scripts, exact)


def floor_unwrap(command: str, check: Callable[[object], bool]) -> str | None:
    inner = unwrapped(command)
    return inner if inner is not None and not check(inner) else None


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
