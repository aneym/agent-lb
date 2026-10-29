from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from floor_invariant import refuse_floor_changes  # type: ignore[missing-import]
from policy_layout import ROUTE_FILES  # type: ignore[missing-import]

STAGED_DIRS = (Path(".agent-lb/bin"), Path(".agent-lb/managed/coding-agents"))
VERIFY_SEAT = "codex-verifier"
EXECUTABLE_NAMES = ("verify-routing", "route", "seat", "claude-box-launch")


def executable(path: Path) -> bool:
    return path.suffix in (".py", ".sh") or path.name in EXECUTABLE_NAMES


def exchange(first: Path, second: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    a, b = os.fsencode(first), os.fsencode(second)
    if sys.platform == "darwin":
        result = libc.renamex_np(a, b, 0x2)
    else:
        result = libc.renameat2(-100, a, -100, b, 0x2)
    if result != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, os.strerror(errno), str(second))


def signature(path: Path) -> tuple:
    info = path.lstat()
    mark = (info.st_mtime_ns, info.st_size, info.st_ino)
    if not path.is_dir() or path.is_symlink():
        return mark
    inner = []
    for root, dirs, files in os.walk(path):
        dirs[:] = [name for name in dirs if name != "__pycache__"]
        for name in files:
            child = os.lstat(os.path.join(root, name))
            inner.append((os.path.relpath(os.path.join(root, name), path), child.st_mtime_ns, child.st_size))
    return mark, tuple(sorted(inner))


def entries(directory: Path) -> dict[str, tuple]:
    if not directory.is_dir():
        return {}
    return {entry.name: signature(Path(entry.path)) for entry in os.scandir(directory) if entry.name != "__pycache__"}


def copy_entry(source: Path, destination: Path) -> None:
    if destination.is_dir() and not destination.is_symlink():
        shutil.rmtree(destination)
    elif destination.exists() or destination.is_symlink():
        destination.unlink()
    if source.is_dir() and not source.is_symlink():
        shutil.copytree(source, destination, symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))
    else:
        shutil.copy2(source, destination, follow_symlinks=False)


def build_stage(live: Path, stage: Path, files: dict[Path, str | None]) -> None:
    stage.mkdir(parents=True)
    if live.is_dir():
        mode = live.stat().st_mode & 0o7777
        for name in entries(live):
            copy_entry(live / name, stage / name)
    else:
        mode = 0o755
    for path, content in files.items():
        target = stage / path.relative_to(live)
        if content is None:
            if target.is_symlink() or target.is_file():
                target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        prior = path if path.exists() else None
        target.unlink(missing_ok=True)
        target.write_text(content)
        mode_bits = prior.stat().st_mode & 0o777 if prior else 0o644
        if path.suffix == ".sh":
            mode_bits = 0o755
        if executable(path):
            mode_bits |= 0o111
        os.chmod(target, mode_bits)
    os.chmod(stage, mode)


def verify_stage(home: Path, bin_dir: Path, managed_dir: Path) -> None:
    modules = [name[:-3] for name in ROUTE_FILES if name.endswith(".py") and (bin_dir / name).is_file()]
    probe = (
        "import importlib, sys\n"
        f"sys.path.insert(0, {str(bin_dir)!r})\n"
        f"for name in {modules!r}:\n"
        "    importlib.import_module(name)\n"
    )
    checks = [("import check", [sys.executable, "-I", "-c", probe])]
    if (home / ".claude/agents" / f"{VERIFY_SEAT}.md").is_file():
        checks.append((f"route seat {VERIFY_SEAT}", [sys.executable, str(bin_dir / "route"), "seat", VERIFY_SEAT]))
    with tempfile.TemporaryDirectory(prefix="policy-stage-verify-") as root:
        (Path(root) / "managed").mkdir()
        (Path(root) / "managed/coding-agents").symlink_to(managed_dir)
        env = dict(
            os.environ,
            HOME=str(home),
            ROUTE_TABLE=str(managed_dir / "routing-table.json"),
            AGENT_LB_POLICY_SRC=str(Path(root) / "policy-src"),
        )
        env.pop("PYTHONPATH", None)
        for label, command in checks:
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=120, env=env, cwd=str(home))
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise SystemExit(f"error: staged install failed {label}: {exc}; live files unchanged") from exc
            if result.returncode:
                tail = (result.stderr.strip() or result.stdout.strip()).splitlines()[-3:]
                raise SystemExit(f"error: staged install failed {label}: {' | '.join(tail)}; live files unchanged")
            print(f"staged {label}: ok")


def swap_in(live: Path, stage: Path, snapshot: dict[str, tuple], owned: set[str]) -> None:
    if live.is_dir():
        current = entries(live)
        for name in set(snapshot) | set(current):
            if name in owned or snapshot.get(name) == current.get(name):
                continue
            if name in current:
                copy_entry(live / name, stage / name)
            else:
                target = stage / name
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                elif target.exists() or target.is_symlink():
                    target.unlink()
        try:
            exchange(stage, live)
        except (OSError, AttributeError):
            retired = stage.with_name(stage.name + ".old")
            os.rename(live, retired)
            try:
                os.rename(stage, live)
            except OSError:
                os.rename(retired, live)
                raise
            stage = retired
        shutil.rmtree(stage, ignore_errors=True)
    else:
        live.parent.mkdir(parents=True, exist_ok=True)
        os.rename(stage, live)
    print(f"swapped in {live}")


def install_staged(home: Path, changes: dict[Path, str | None], uninstall: bool) -> dict[Path, str | None]:
    refuse_floor_changes(home, changes, uninstall)
    roots = [(home / relative) for relative in STAGED_DIRS]
    lives = [root.resolve() if root.is_symlink() else root for root in roots]
    grouped: dict[Path, dict[Path, str | None]] = {live: {} for live in lives}
    rest: dict[Path, str | None] = {}
    for path, content in changes.items():
        for root, live in zip(roots, lives):
            if path.parent == root:
                grouped[live][live / path.name] = content
                break
        else:
            rest[path] = content
    if not any(grouped.values()):
        return rest
    stages = {live: live.with_name(f".{live.name}.stage-{os.getpid()}") for live in lives if grouped[live]}
    snapshots = {}
    try:
        for live, stage in stages.items():
            if stage.exists():
                shutil.rmtree(stage)
            snapshots[live] = entries(live)
            build_stage(live, stage, grouped[live])
        bin_dir, managed_dir = (stages.get(live, live) for live in lives)
        verify_stage(home, bin_dir, managed_dir)
        for live, stage in stages.items():
            swap_in(live, stage, snapshots[live], {path.name for path in grouped[live]})
            for path, content in grouped[live].items():
                print(f"{'removed' if content is None else 'updated'} {path}")
    finally:
        for stage in stages.values():
            shutil.rmtree(stage, ignore_errors=True)
    return rest
