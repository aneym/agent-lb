from __future__ import annotations

import json
import os
import re
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

TAIL_BYTES = 2 * 1024 * 1024
TRUNCATED = "... (truncated)"
STUDIO_ENVIRONMENT = "78038392-71c3-4cd9-bfc8-19fc12031eee"


def local_host() -> str:
    configured = os.environ.get("FACTORY_HOST") or socket.gethostname()
    return configured.split(".")[0].lower()


def t3_selection(payload: dict) -> tuple:
    selected = payload.get("t3")
    selected = selected if isinstance(selected, dict) else {}
    thread_id = str(os.environ.get("T3_THREAD_ID") or selected.get("thread_id") or "")
    environment = os.environ.get("T3_ENVIRONMENT_ID")
    if environment:
        host = "studio" if environment == STUDIO_ENVIRONMENT else local_host()
    else:
        host = str(selected.get("host") or "")
    return thread_id, host


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


def identity(payload: dict, lanes: Path, agents: Path, thread_id: str, host: str) -> tuple:
    lane = os.environ.get("FACTORY_LANE", "")
    if lane and (lanes / lane).is_dir():
        return lane, "", read_object(lanes / lane / "lane.json")
    records = lane_records(lanes)
    if thread_id:
        for _, name, data in records:
            t3 = data.get("t3")
            if isinstance(t3, dict) and t3.get("thread_id") == thread_id and (not host or t3.get("host") == host):
                return name, "", data
    for path in sorted(agents.glob("*/agent.json")):
        data = read_object(path)
        t3 = data.get("t3")
        if (
            thread_id
            and isinstance(t3, dict)
            and t3.get("thread_id") == thread_id
            and (not host or t3.get("host") == host)
        ):
            return "", data.get("name") or path.parent.name, data
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
    if within(cwd, agents.resolve()):
        parts = cwd.relative_to(agents.resolve()).parts
        if parts and (agents / parts[0]).is_dir():
            return "", parts[0], read_object(agents / parts[0] / "agent.json")
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
    preview = next((line for line in lines if line.strip() and not line.startswith("#")), "")
    return [f"Lane state: {preview[:120]} (full: cat {path})"]


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


def learning_lines(path: Path, thread_id: str, lane: str, project: str) -> list:
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
        rank = 0 if thread_id and row.get("by") == thread_id else (1 if any(name in content for name in names) else 2)
        text = "- [{}] {} (full: cat {})".format(row.get("id") or row.get("at", "-"), problem[:80], path)
        ranked.append((rank, index, text.replace("\r", " ").replace("\n", " ")[:300]))
    selected = sorted(ranked, key=lambda item: (item[0], -item[1]))[:3]
    if not selected:
        return []
    return ["## Recent factory learnings (factory learn)"] + [
        item[2] for item in sorted(selected, key=lambda item: item[1])
    ]


STALE_SECONDS = 6 * 3600
BOUNDARY_SCAN = 32 * 1024 * 1024


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", value)[:120] or "unknown"


def agent_dir(agents: Path, agent: str):
    if (agents / agent / "agent.json").is_file():
        return agents / agent
    for path in sorted(agents.glob("*/agent.json")):
        if read_object(path).get("name") == agent:
            return path.parent
    return None


def unmigrated_agents(agents: Path) -> list[str]:
    names = []
    for path in sorted(agents.glob("*/agent.json")):
        data = read_object(path)
        if not isinstance(data.get("t3"), dict):
            names.append(data.get("name") or path.parent.name)
    return [name for name in names if isinstance(name, str)]


def checkpoint_path(lanes: Path, agents: Path, lane: str, agent: str, session: str) -> tuple:
    if lane:
        home = lanes / lane
        return home / "compact-checkpoint.json", {"kind": "lane", "name": lane, "dir": str(home)}
    home = agent_dir(agents, agent) if agent else None
    if home is not None:
        return home / "compact-checkpoint.json", {"kind": "agent", "name": agent, "dir": str(home)}
    fallback = Path(
        os.environ.get("FACTORY_COMPACT_CHECKPOINTS_DIR") or "~/.agent-rails/factory/hooks/compact-checkpoints"
    ).expanduser()
    return fallback / "{}.json".format(safe_name(session)), {"kind": "none", "name": "", "dir": str(fallback)}


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
        written = datetime.fromisoformat(str(data.get("ts")).replace("Z", "+00:00"))
        age = (now - written).total_seconds()
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
            "Compact checkpoint {} is stale ({}; age {}). Listing none of its items; rebuild from state and "
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
            lines.append(
                "- open seat {} {}".format(
                    entry.get("name") or entry.get("subagent_type") or "-",
                    entry.get("agent_id") or entry.get("tool_use_id"),
                )
            )
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
    thread_id, host = t3_selection(payload)
    lane, agent, data = identity(payload, lanes, agents, thread_id, host)
    binding = data.get("t3")
    binding = binding if isinstance(binding, dict) else {}
    thread_id = str(binding.get("thread_id") or "")
    host = str(binding.get("host") or "")
    session = str(payload.get("session_id") or "")
    path, _ = checkpoint_path(lanes, agents, lane, agent, session)
    if not lane and not agent:
        legacy = unmigrated_agents(agents)
        gap = "; unmigrated agent records without verified T3 binding: {}".format(len(legacy)) if legacy else ""
        line = (
            "Factory context: no lane or agent home resolved (cwd {}, T3 thread {}, host {}); compact checkpoint {}: {}.".format(
                payload.get("cwd") or os.getcwd(),
                thread_id or "-",
                host or "-",
                "found" if path.is_file() else "none",
                path,
            )
            + gap
        )
        text = "\n".join(cap_lines([line[:1000]], 1024))
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
        return
    project = str(data.get("project") or "")
    state = []
    if lane:
        home = lanes / lane
        base = [
            "Factory role: lead of lane {} (project {}, T3 thread {}, host {}). Context re-injected after {}.".format(
                lane, project or "-", thread_id or "-", host or "-", source
            ),
            "Lane state: {}/STATE.md. Re-read it in full and append decisions there before acting.".format(home),
        ]
        if (home / "specs").is_dir():
            base.append("Specs: {}/specs/".format(home))
        if (home / "STATE.md").is_file():
            state = state_lines(home / "STATE.md")
    else:
        base = [
            "Factory role: agent home {} (T3 thread {}, host {}). Context re-injected after {}.".format(
                agent, thread_id or "-", host or "-", source
            )
        ]
        home = agent_dir(agents, agent)
        card = thread_card(home, session) if home is not None else None
        base.append("Thread card: {}. Run memory.brief before acting.".format(card or "none found"))
    if source == "compact":
        base = base + cap_lines(
            checkpoint_lines(path, payload, datetime.now(timezone.utc)), 1023 - len("\n".join(base).encode())
        )
    learnings = learning_lines(learned, thread_id, lane, project)
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
