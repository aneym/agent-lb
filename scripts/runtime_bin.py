from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from scripts.runtime_tree import Paths, git

SOURCES = ("scripts", "clients")


@dataclass
class BinReport:
    refreshed: list[str] = field(default_factory=list)
    drift: list[str] = field(default_factory=list)
    current: int = 0


def blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def copies(paths: Paths) -> list[tuple[Path, Path, str]]:
    found = []
    if not paths.bin_dir.is_dir():
        return found
    for entry in sorted(paths.bin_dir.iterdir()):
        if entry.name.startswith(".") or ".bak" in entry.name or entry.is_symlink() or not entry.is_file():
            continue
        for base in SOURCES:
            rel = f"{base}/{entry.name}"
            staged = paths.stage / rel
            if staged.is_file():
                found.append((entry, staged, rel))
                break
    return found


def history(paths: Paths, rel: str) -> set[str]:
    out = git(paths, "log", "--format=", "--raw", "--no-abbrev", "refs/remotes/origin/main", "--", rel, timeout=90)
    blobs = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and line.startswith(":"):
            blobs.update(parts[2:4])
    return blobs


def plan(paths: Paths, verdicts: dict[str, str]) -> tuple[list[tuple[Path, Path, str]], BinReport]:
    report = BinReport()
    todo = []
    for installed, staged, rel in copies(paths):
        have = blob_id(installed.read_bytes())
        if have == blob_id(staged.read_bytes()):
            report.current += 1
            continue
        key = f"{rel}@{have}"
        verdict = verdicts.get(key)
        if verdict is None:
            verdict = "outdated" if have in history(paths, rel) else "drift"
            verdicts[key] = verdict
        if verdict == "outdated":
            todo.append((installed, staged, rel))
        else:
            report.drift.append(installed.name)
    return todo, report


def save(todo: list[tuple[Path, Path, str]], dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for installed, _, _ in todo:
        shutil.copy2(installed, dest / installed.name)


def install(todo: list[tuple[Path, Path, str]], report: BinReport) -> None:
    for installed, staged, _ in todo:
        temp = installed.with_name(f".{installed.name}.follow")
        shutil.copyfile(staged, temp)
        os.chmod(temp, 0o755)
        os.replace(temp, installed)
        report.refreshed.append(installed.name)


def put_back(saved: Path, bin_dir: Path) -> None:
    if not saved.is_dir():
        return
    for item in saved.iterdir():
        temp = bin_dir / f".{item.name}.follow"
        shutil.copy2(item, temp)
        os.replace(temp, bin_dir / item.name)
