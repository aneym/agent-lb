"""R028: Re-inject factory role, lane state and learnings after compact or resume.
install-policy.py installs this SessionStart hook.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

TAIL_BYTES = 2 * 1024 * 1024
TRUNCATED = "... (truncated)"


def read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def lane_records(lanes: Path) -> list:
    records = []
    for path in lanes.glob("*/lane.json"):
        try:
            records.append((path.stat().st_mtime_ns, path.parent.name, read_object(path)))
        except OSError:
            continue
    return sorted(records, key=lambda item: (item[0], item[1]), reverse=True)


def identity(payload: dict, lanes: Path, agents: Path, pane: str) -> tuple:
    lane = os.environ.get("FACTORY_LANE", "")
    if lane and (lanes / lane).is_dir():
        return lane, "", read_object(lanes / lane / "lane.json")
    records = lane_records(lanes)
    if pane:
        for _, name, data in records:
            herdr = data.get("herdr")
            if isinstance(herdr, dict) and herdr.get("pane_id") == pane:
                return name, "", data
        for path in sorted(agents.glob("*/agent.json")):
            data = read_object(path)
            if data.get("pane") == pane:
                return "", data.get("name") or path.parent.name, {}
    cwd = Path(payload.get("cwd") or os.getcwd()).resolve()
    root = lanes.resolve()
    if within(cwd, root):
        parts = cwd.relative_to(root).parts
        if parts and (root / parts[0]).is_dir():
            return parts[0], "", read_object(root / parts[0] / "lane.json")
    for _, name, data in records:
        worktrees = data.get("worktrees", [])
        if not isinstance(worktrees, list):
            continue
        for entry in worktrees:
            path = entry.get("path") if isinstance(entry, dict) else entry
            if isinstance(path, str) and path and within(cwd, Path(path)):
                return name, "", data
    return "", "", {}


def cap_lines(lines: list, limit: int) -> list:
    if len("\n".join(lines).encode()) <= limit:
        return lines
    kept = []
    for line in lines:
        candidate = kept + [line, TRUNCATED]
        if len("\n".join(candidate).encode()) > limit:
            break
        kept.append(line)
    return kept + [TRUNCATED] if limit >= len(TRUNCATED) else []


def state_lines(path: Path) -> list:
    try:
        with path.open() as stream:
            lines = []
            for index, line in enumerate(stream):
                if index == 60:
                    break
                lines.append(line.rstrip("\r\n")[:400])
    except OSError:
        return []
    preview = next((line for line in lines if line.strip() and not line.startswith('#')), '')
    return [f'Lane state: {preview[:120]} (full: cat {path})']


def expired(value: object, now: datetime) -> bool:
    if not isinstance(value, str):
        return False
    prefix = re.match(
        r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?",
        value,
    )
    if prefix is None:
        return False
    try:
        date = datetime.fromisoformat(prefix.group().replace("Z", "+00:00"))
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return date < now
    except ValueError:
        return False


def learning_lines(path: Path, pane: str, lane: str, project: str) -> list:
    try:
        with path.open("rb") as stream:
            size = stream.seek(0, 2)
            stream.seek(max(0, size - TAIL_BYTES))
            if size > TAIL_BYTES:
                stream.readline()
            raw = stream.read()
    except OSError:
        return []
    names = [name.lower() for name in (lane, project) if len(name) >= 3]
    ranked = []
    now = datetime.now(timezone.utc)
    for index, line in enumerate(raw.decode("utf-8", errors="replace").splitlines()):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or expired(row.get("expires"), now):
            continue
        problem, fix = str(row.get("problem", "")), str(row.get("fix", ""))
        content = (problem + " " + fix).lower()
        rank = 0 if pane and row.get("by") == pane else (1 if any(name in content for name in names) else 2)
        text = "- [{}] {} (full: cat {})".format(row.get("id") or row.get("at", "-"), problem[:80], path)
        ranked.append((rank, index, text.replace("\r", " ").replace("\n", " ")[:300]))
    selected = sorted(ranked, key=lambda item: (item[0], -item[1]))[:3]
    if not selected:
        return []
    return ["## Recent factory learnings (factory learn)"] + [
        item[2] for item in sorted(selected, key=lambda item: item[1])
    ]


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    source = payload.get("source")
    if source not in ("compact", "resume"):
        return
    lanes = Path(os.environ.get("FACTORY_LANES_DIR") or "~/.agent-rails/lanes").expanduser()
    agents = Path(os.environ.get("FACTORY_AGENTS_DIR") or "~/.agent-rails/agents").expanduser()
    learned = Path(os.environ.get("FACTORY_LEARNED_FILE") or "~/.agent-rails/factory/learned.jsonl").expanduser()
    pane = os.environ.get("HERDR_PANE_ID", "")
    lane, agent, data = identity(payload, lanes, agents, pane)
    if not lane and not agent:
        return
    project = str(data.get("project") or "")
    state = []
    if lane:
        home = lanes / lane
        base = [
            "Factory role: lead of lane {} (project {}, herdr pane {}). Context re-injected after {}.".format(
                lane, project or "-", pane or "-", source
            ),
            "Lane state: {}/STATE.md. Re-read it in full and append decisions there before acting.".format(home),
        ]
        if (home / "specs").is_dir():
            base.append("Specs: {}/specs/".format(home))
        if (home / "STATE.md").is_file():
            state = state_lines(home / "STATE.md")
    else:
        base = [
            "Factory role: agent home {} (herdr pane {}). Context re-injected after {}.".format(agent, pane, source)
        ]
    learnings = learning_lines(learned, pane, lane, project)
    while learnings and len("\n".join(base + state + learnings).encode()) > 1024:
        learnings.pop()
        if len(learnings) == 1:
            learnings = []
    budget = 1024 - len("\n".join(base + learnings).encode()) - bool(base and state) - bool(learnings and state)
    state = cap_lines(state, budget)
    text = "\n".join(cap_lines(base + state + learnings, 1024))
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
