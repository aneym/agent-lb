from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from datetime import timezone
from pathlib import Path

TRUNCATED = "... (truncated)"
STALE_SECONDS = 6 * 3600
BOUNDARY_SCAN = 32 * 1024 * 1024
AGENT_HOMES = Path("~/.agent-rails/agents").expanduser()
CHECKPOINTS = Path("~/.claude/hooks/state/compact-checkpoints").expanduser()
ORCH_LINE = "You are an orch. Load ~/.claude/skills/orch/SKILL.md and run memory.brief before acting."


def read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def t3_selection(payload: dict) -> tuple:
    selected = payload.get("t3")
    selected = selected if isinstance(selected, dict) else {}
    thread_id = str(os.environ.get("T3_THREAD_ID") or selected.get("thread_id") or "")
    environment = str(os.environ.get("T3_ENVIRONMENT_ID") or selected.get("environment_id") or "")
    return thread_id, environment


def bound(record: dict, thread_id: str, environment: str) -> bool:
    t3 = record.get("t3")
    if not thread_id or not isinstance(t3, dict) or t3.get("thread_id") != thread_id:
        return False
    return not environment or not t3.get("environment_id") or t3.get("environment_id") == environment


def identity(payload: dict, agents: Path, thread_id: str, environment: str) -> tuple:
    for path in sorted(agents.glob("*/agent.json")):
        record = read_object(path)
        if bound(record, thread_id, environment):
            return path.parent, record
    cwd = Path(payload.get("cwd") or os.getcwd()).resolve()
    try:
        parts = cwd.relative_to(agents.resolve()).parts
    except ValueError:
        parts = ()
    if parts and (agents / parts[0] / "agent.json").is_file():
        return agents / parts[0], read_object(agents / parts[0] / "agent.json")
    return None, {}


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", value)[:120] or "unknown"


def checkpoint_path(home, record: dict, session: str) -> tuple:
    if home is not None:
        name = record.get("name") or home.name
        return home / "compact-checkpoint.json", {"kind": "agent", "name": name, "dir": str(home)}
    return CHECKPOINTS / "{}.json".format(safe_name(session)), {"kind": "none", "name": "", "dir": str(CHECKPOINTS)}


def cap_lines(lines: list, limit: int) -> list:
    if len("\n".join(lines).encode()) <= limit:
        return lines
    kept = []
    for line in lines:
        if len("\n".join(kept + [line, TRUNCATED]).encode()) > limit:
            break
        kept.append(line)
    return kept + [TRUNCATED] if limit >= len(TRUNCATED) else []


def boundaries_after(transcript: str, offset) -> int:
    if not transcript or not isinstance(offset, int):
        return -1
    try:
        with open(transcript, "rb") as stream:
            size = stream.seek(0, 2)
            if size < offset or size - offset > BOUNDARY_SCAN:
                return -1
            stream.seek(offset)
            data = stream.read()
    except OSError:
        return -1
    return sum(1 for line in data.splitlines() if b'"compact_boundary"' in line)


def age_text(seconds: float) -> str:
    return "{} min".format(int(seconds // 60)) if seconds < 7200 else "{:.1f} h".format(seconds / 3600)


def checkpoint_lines(path: Path, payload: dict, now: datetime) -> list:
    if not path.is_file():
        return ["Compact checkpoint: none at {}.".format(path)]
    data = read_object(path)
    try:
        age = (now - datetime.fromisoformat(str(data.get("ts")).replace("Z", "+00:00"))).total_seconds()
    except ValueError:
        age = None
    reasons = []
    if age is None or age > STALE_SECONDS:
        reasons.append("older than 6 h" if age is not None else "no readable ts")
    if str(data.get("session_id") or "") != str(payload.get("session_id") or ""):
        reasons.append("session_id differs")
    transcript = data.get("transcript") if isinstance(data.get("transcript"), dict) else {}
    count = boundaries_after(
        str(payload.get("transcript_path") or transcript.get("path") or ""), transcript.get("offset")
    )
    if count > 1:
        reasons.append("a newer compact boundary follows its offset")
    shown = age_text(age) if age is not None else "unknown"
    if reasons:
        return [
            "Compact checkpoint {} is stale ({}; age {}). Listing none of its items; rebuild from the thread and "
            "the transcript.".format(path, ", ".join(reasons), shown)
        ]
    lines = [
        "Compact checkpoint: {} (age {}). Reconcile before new work; approval ids are references, "
        "never consent.".format(path, shown)
    ]
    for entry in (data.get("bash") or [])[-2:]:
        if isinstance(entry, dict):
            lines.append("- cmd in {}: {}".format(entry.get("cwd") or "-", str(entry.get("command"))[:160]))
    for entry in data.get("background_tasks") or []:
        if isinstance(entry, dict):
            lines.append("- running task {}".format(entry.get("task_id")))
    for entry in data.get("agents") or []:
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("subagent_type") or "-"
            lines.append("- open agent {} {}".format(name, entry.get("agent_id") or entry.get("tool_use_id")))
    for key, label in (("ask_ids", "ask"), ("approval_ids", "approval ref"), ("grant_ids", "grant")):
        for entry in (data.get(key) or [])[-4:]:
            if isinstance(entry, dict):
                lines.append("- {} {}".format(label, entry.get("id")))
    return lines[:1] + [line.replace("\r", " ").replace("\n", " ")[:240] for line in lines[1:]]


def thread_card(home: Path, session: str):
    cards = []
    for path in home.glob("threads/*/thread.json"):
        try:
            cards.append((path.stat().st_mtime_ns, path))
        except OSError:
            continue
    for _, path in sorted(cards, reverse=True):
        if session and read_object(path).get("harness_session_id") == session:
            return path
    return max(cards)[1] if cards else None


def emit(lines: list) -> None:
    if lines:
        text = "\n".join(cap_lines(lines, 1024))
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    source = payload.get("source")
    if source not in ("startup", "compact", "resume"):
        return
    thread_id, environment = t3_selection(payload)
    home, record = identity(payload, AGENT_HOMES, thread_id, environment)
    orch = record.get("role") == "orch" and bound(record, thread_id, environment)
    if source == "startup":
        emit([ORCH_LINE] if orch else [])
        return
    session = str(payload.get("session_id") or "")
    lines = [ORCH_LINE] if orch else []
    if home is not None:
        card = thread_card(home, session)
        lines.append(
            "Agent home {} (T3 thread {}). Context re-injected after {}. Thread card: {}.".format(
                record.get("name") or home.name, thread_id or "-", source, card or "none found"
            )
        )
    if source == "compact":
        path, _ = checkpoint_path(home, record, session)
        lines += cap_lines(
            checkpoint_lines(path, payload, datetime.now(timezone.utc)), 1023 - len("\n".join(lines).encode())
        )
    emit(lines)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
