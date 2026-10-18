from __future__ import annotations

import gzip
import json
from pathlib import Path

from app.core import conversation_archive


def _reset_archive_disk_pressure() -> None:
    with conversation_archive._DISK_PRESSURE_LOCK:
        conversation_archive._DISK_PRESSURE_PAUSED_UNTIL = 0.0
        conversation_archive._DISK_PRESSURE_LAST_WARNING_AT = (
            -conversation_archive._DISK_PRESSURE_WARNING_INTERVAL_SECONDS
        )


class _ArchiveSettings:
    def __init__(self, *, enabled: bool, directory: Path, queue_max_bytes: int = 8 * 1024 * 1024) -> None:
        self.conversation_archive_enabled = enabled
        self.conversation_archive_dir = directory
        self.conversation_archive_queue_max_bytes = queue_max_bytes


def _archive_records(directory: Path) -> list[dict[str, object]]:
    files = sorted(directory.glob("*.jsonl.gz"))
    assert len(files) == 1
    with gzip.open(files[0], "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh.read().splitlines()]


def _archive_lines(directory: Path) -> list[str]:
    files = sorted(directory.glob("*.jsonl.gz"))
    assert len(files) == 1
    with gzip.open(files[0], "rt", encoding="utf-8") as fh:
        return fh.read().splitlines()


def _write_gzip_record(path: Path, payload: dict[str, object]) -> None:
    with gzip.open(path, "at", encoding="utf-8") as fh:
        fh.write(json.dumps(payload) + "\n")
