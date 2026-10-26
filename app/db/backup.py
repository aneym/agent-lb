from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path


def _backup_name(source: Path, timestamp: datetime) -> str:
    return f"{source.stem}.pre-migrate-{timestamp.strftime('%Y%m%dT%H%M%SZ')}{source.suffix}"


def _next_backup_path(source: Path, timestamp: datetime) -> Path:
    base_name = _backup_name(source, timestamp)
    candidate = source.parent / base_name
    if not candidate.exists():
        return candidate

    sequence = 1
    while True:
        name = f"{source.stem}.pre-migrate-{timestamp.strftime('%Y%m%dT%H%M%SZ')}-{sequence}{source.suffix}"
        candidate = source.parent / name
        if not candidate.exists():
            return candidate
        sequence += 1


def _file_state(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def list_sqlite_pre_migration_backups(source: Path) -> list[Path]:
    pattern = f"{source.stem}.pre-migrate-*{source.suffix}"
    return sorted((path for path in source.parent.glob(pattern) if path.is_file()))


def create_sqlite_pre_migration_backup(
    source: Path,
    *,
    max_files: int,
    now: datetime | None = None,
) -> Path:
    if max_files < 1:
        raise ValueError("max_files must be >= 1")
    if not source.exists():
        raise FileNotFoundError(f"sqlite database not found: {source}")

    state = _file_state(source)
    backups = list_sqlite_pre_migration_backups(source)
    same = [path for path in backups if _file_state(path) == state]
    if same:
        backup_path = same[-1]
    else:
        timestamp = now or datetime.now(timezone.utc)
        backup_path = _next_backup_path(source, timestamp)
        shutil.copy2(source, backup_path)

    for duplicate in same[:-1]:
        duplicate.unlink()
    backups = list_sqlite_pre_migration_backups(source)
    excess = len(backups) - max_files
    if excess > 0:
        for old_backup in backups[:excess]:
            old_backup.unlink()

    return backup_path
