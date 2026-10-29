from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path, PurePosixPath
from typing import Any

from codex_home import OFFLOAD_COMMAND  # type: ignore[missing-import]
from floor_hooks import FLOOR_STEMS, NON_FLOOR_GUARDS, non_floor, stem, unwrapped  # type: ignore[missing-import]
from policy_layout import SETTINGS_HOOKS  # type: ignore[missing-import]

SOURCE_HOOKS = Path(__file__).resolve().parent / "hooks"


def git(top: Path, *args: str, data: bytes | None = None) -> bytes | None:
    try:
        result = subprocess.run(["git", "-C", str(top), *args], input=data, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def checkout(path: Path) -> tuple[Path, str] | None:
    top = git(path, "rev-parse", "--show-toplevel")
    sha = git(path, "rev-parse", "--verify", "HEAD^{commit}")
    if top is None or sha is None:
        return None
    return Path(os.path.realpath(top.decode().strip())), sha.decode().strip()


def committed(top: Path, sha: str, prefix: str) -> dict[str, bytes]:
    listing = git(top, "ls-tree", "-r", "-z", sha, "--", prefix)
    if not listing:
        return {}
    blobs = {}
    for row in listing.split(b"\0"):
        meta, _, name = row.partition(b"\t")
        parts = meta.split()
        if len(parts) == 3 and parts[0] in (b"100644", b"100755") and parts[1] == b"blob":
            blobs[name.decode()] = parts[2].decode()
    found = {}
    for name, oid in blobs.items():
        data = git(top, "cat-file", "blob", oid)
        if data is not None:
            found[name] = data
    return found


def claude_config(home: Path) -> tuple[Path, str] | None:
    candidates = [home / ".claude", Path(os.path.realpath(home / ".local/bin/gh")).parent.parent]
    for candidate in candidates:
        found = checkout(candidate) if (candidate / "hooks.json").is_file() else None
        if found and found[0] == Path(os.path.realpath(candidate)):
            return found
    return None


def shipped_manifest(home: Path) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    found = claude_config(home)
    if found is None:
        return [], {}
    top, sha = found
    try:
        manifest = json.loads(committed(top, sha, "hooks.json").get("hooks.json") or b"")
    except ValueError:
        return [], {}
    entries = manifest.get("hooks") if isinstance(manifest, dict) else None
    entries = [entry for entry in entries or [] if isinstance(entry, dict)]
    return entries, committed(top, sha, "hooks")


def shipped_files(home: Path) -> Iterator[tuple[str, bytes, list[Path]]]:
    hooks = home / ".claude/hooks"
    found = checkout(SOURCE_HOOKS)
    if found is not None:
        top, sha = found
        prefix = os.path.relpath(SOURCE_HOOKS, top)
        for name, data in committed(top, sha, prefix).items():
            relative = PurePosixPath(name).relative_to(prefix)
            if len(relative.parts) == 1:
                yield relative.name, data, [top / name, hooks / relative.name]
    entries, blobs = shipped_manifest(home)
    names = {name for entry in entries for name in entry.get("files") or [] if isinstance(name, str)}
    names |= {f"hooks/{name}" for name in NON_FLOOR_GUARDS if f"hooks/{name}" in blobs}
    for name in sorted(names):
        relative = PurePosixPath(name)
        if name in blobs and relative.parts[:1] == ("hooks",) and len(relative.parts) == 2:
            yield relative.name, blobs[name], [hooks / relative.name]


def plain(target: Path) -> str | None:
    if target.is_symlink():
        return None
    real = os.path.realpath(target)
    return real if real == os.path.join(os.path.realpath(target.parent), target.name) else None


def allowed_scripts(home: Path) -> dict[str, set[str]]:
    scripts: dict[str, set[str]] = {}
    for name, data, targets in shipped_files(home):
        if stem(name) in FLOOR_STEMS:
            continue
        value = hashlib.sha256(data).hexdigest()
        for target in targets:
            real = plain(target)
            if real:
                scripts.setdefault(real, set()).add(value)
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
