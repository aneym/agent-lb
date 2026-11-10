from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

OPEN_TURN_HOURS = 12
OWNER = "owner"

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}\Z")
_SESSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


@dataclass(frozen=True, slots=True)
class Turn:
    turn: str
    session: str
    asker: str
    start: datetime
    end: datetime
    open: bool


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _record(line: str) -> dict[str, Any] | None:
    try:
        record = json.loads(line)
    except ValueError:
        return None
    if not isinstance(record, dict) or record.get("v") != 1:
        return None
    turn, session, asker = record.get("turn"), record.get("session"), record.get("asker")
    if not (isinstance(turn, str) and _ID.fullmatch(turn)):
        return None
    if not (isinstance(session, str) and _SESSION.fullmatch(session)):
        return None
    if not (isinstance(asker, str) and _ID.fullmatch(asker)) or asker == OWNER:
        return None
    start = _utc(record.get("start"))
    end = None if record.get("end") is None else _utc(record.get("end"))
    if start is None or (record.get("end") is not None and (end is None or end <= start)):
        return None
    return {"turn": turn, "session": session, "asker": asker, "start": start, "end": end}


def pull(sources: list[str], directory: Path) -> list[dict[str, Any]]:
    pulled: list[dict[str, Any]] = []
    for source in sources:
        host, _, remote = source.partition(":")
        if not (_HOST.fullmatch(host) and remote):
            pulled.append({"source": source, "error": "not host:dir"})
            continue
        target = directory / host
        target.mkdir(parents=True, exist_ok=True)
        command = [
            "rsync",
            "--recursive",
            "--times",
            "--delete",
            "--timeout=30",
            "--include=*/",
            "--include=*.jsonl",
            "--exclude=*",
            "-e",
            "ssh -o BatchMode=yes -o ConnectTimeout=10",
            f"{host}:{remote.rstrip('/')}/",
            f"{target}/",
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=90)
        except subprocess.TimeoutExpired:
            pulled.append({"source": source, "error": "rsync timed out; kept the last copy"})
            continue
        error = None if result.returncode == 0 else f"rsync exited {result.returncode}; kept the last copy"
        pulled.append({"source": source, "error": error})
    return pulled


def load(directory: Path | None, now: datetime) -> tuple[list[Turn], dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    files = lines = rejected = 0
    if directory is not None and directory.is_dir():
        for path in sorted(directory.rglob("*.jsonl")):
            if not path.is_file():
                continue
            files += 1
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                lines += 1
                record = _record(line)
                if record is None:
                    rejected += 1
                    continue
                latest[record["turn"]] = record
    cap = timedelta(hours=OPEN_TURN_HOURS)
    turns = [
        Turn(
            turn=record["turn"],
            session=record["session"],
            asker=record["asker"],
            start=record["start"],
            end=record["end"] or min(now, record["start"] + cap),
            open=record["end"] is None,
        )
        for record in latest.values()
    ]
    turns = [turn for turn in turns if turn.end > turn.start]
    summary = {
        "directory": str(directory) if directory is not None else None,
        "files": files,
        "lines": lines,
        "rejected_lines": rejected,
        "turns": len(turns),
        "open_turns": sum(1 for turn in turns if turn.open),
        "sessions": len({turn.session for turn in turns}),
    }
    return turns, summary
