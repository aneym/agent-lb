#!/usr/bin/env python3


from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
STATE_DIR = Path(os.environ.get("IDLE_AGENT_STATE_DIR") or HOME / ".claude" / "state")
REGISTRY = STATE_DIR / "idle-agents.json"
LOCK = STATE_DIR / "idle-agents.lock"
LOG = STATE_DIR / "idle-agents.log"
KEEP_FILE = STATE_DIR / "keep-agents.txt"
TEAMS = HOME / ".claude" / "teams"
PROJECTS = HOME / ".claude" / "projects"
ORCH_DIR = Path(
    os.environ.get("IDLE_AGENT_ORCH_DIR") or HOME / ".agent-rails" / "scoping" / "factory-program" / "CONTEXT"
)

IDLE_MIN = float(os.environ.get("IDLE_AGENT_MIN", "6"))
DRY_RUN = os.environ.get("IDLE_REAPER_DRY_RUN", "0") == "1"
TAIL_BYTES = 256 * 1024


def now() -> float:
    return float(os.environ["IDLE_AGENT_NOW"]) if "IDLE_AGENT_NOW" in os.environ else time.time()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_ts(value) -> float | None:
    if isinstance(value, (int, float)):
        return value / 1000 if value > 1e12 else float(value)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def log(line: str) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as handle:
            handle.write(f"{iso(now())} {line}\n")
    except OSError:
        pass


class Registry:
    def __enter__(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._lock = LOCK.open("a")
        fcntl.flock(self._lock, fcntl.LOCK_EX)
        try:
            self.data = json.loads(REGISTRY.read_text())
            if not isinstance(self.data, dict):
                self.data = {}
        except (OSError, ValueError):
            self.data = {}
        for key in ("idle_at", "lead_msg", "nudged", "session_team", "orch_hash", "gone"):
            self.data.setdefault(key, {})
        self._saved = json.dumps(self.data, sort_keys=True)
        return self

    def save(self) -> None:
        serialized = json.dumps(self.data, sort_keys=True)
        if serialized == self._saved:
            return
        tmp = REGISTRY.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True))
        os.replace(tmp, REGISTRY)
        self._saved = serialized

    def __exit__(self, *exc):
        fcntl.flock(self._lock, fcntl.LOCK_UN)
        self._lock.close()
        return False


def session_dir_for(payload: dict) -> Path | None:
    transcript = payload.get("transcript_path")
    sid = payload.get("session_id")
    if transcript and sid:
        candidate = Path(transcript).parent / sid
        if candidate.is_dir():
            return candidate
    if sid:
        for found in PROJECTS.glob(f"*/{sid}"):
            if found.is_dir():
                return found
    return None


def metas(session_dir: Path) -> list[tuple[float, Path, dict]]:
    out = []
    sub = session_dir / "subagents"
    if not sub.is_dir():
        return out
    for meta in sub.glob("agent-*.meta.json"):
        try:
            data = json.loads(meta.read_text())
        except (OSError, ValueError):
            continue
        if data.get("taskKind") != "in_process_teammate" or not data.get("name"):
            continue
        out.append((meta.stat().st_mtime, meta, data))
    return out


def team_for(session_id: str, session_dir: Path, reg: dict) -> str | None:
    cached = reg["session_team"].get(session_id)
    if cached and (TEAMS / cached / "config.json").exists():
        return cached
    for config in TEAMS.glob("*/config.json"):
        try:
            if json.loads(config.read_text()).get("leadSessionId") == session_id:
                reg["session_team"][session_id] = config.parent.name
                return config.parent.name
        except (OSError, ValueError):
            continue
    newest = max(metas(session_dir), key=lambda item: item[0], default=None)
    if newest and newest[2].get("teamName"):
        reg["session_team"][session_id] = newest[2]["teamName"]
        return newest[2]["teamName"]
    return None


def transcript_state(path: Path) -> tuple[str, float | None]:

    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, size - TAIL_BYTES))
            lines = handle.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return "unknown", None
    for raw in reversed(lines):
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        kind = entry.get("type")
        if kind not in ("assistant", "user"):
            continue
        ts = parse_ts(entry.get("timestamp"))
        if kind == "user":
            return "running", ts
        message = entry.get("message") or {}
        content = message.get("content")
        has_tool = isinstance(content, list) and any(
            isinstance(block, dict) and block.get("type") == "tool_use" for block in content
        )
        stop = message.get("stop_reason")
        if has_tool or stop in (None, "tool_use", "pause_turn"):
            return "running", ts
        return "idle", ts
    return "unknown", None


def running_workflow_names(session_dir: Path) -> set[str]:
    names: set[str] = set()
    for wf in (session_dir / "workflows").glob("wf_*.json"):
        try:
            if now() - wf.stat().st_mtime > 24 * 3600:
                continue
            data = json.loads(wf.read_text())
        except (OSError, ValueError):
            continue
        if data.get("status") not in ("running", "pending", "in_progress"):
            continue
        if data.get("workflowName"):
            names.add(str(data["workflowName"]))
        for item in data.get("workflowProgress") or []:
            if isinstance(item, dict):
                for key in ("label", "name", "agentLabel"):
                    if isinstance(item.get(key), str):
                        names.add(item[key])
    return names


def keep_names() -> set[str]:
    try:
        lines = KEEP_FILE.read_text().splitlines()
    except OSError:
        return set()
    return {line.split("#", 1)[0].strip() for line in lines if line.split("#", 1)[0].strip()}


def inbox_recent(team: str, name: str, cutoff: float) -> str | None:
    try:
        messages = json.loads((TEAMS / team / "inboxes" / f"{name}.json").read_text())
    except (OSError, ValueError):
        return None
    for msg in messages if isinstance(messages, list) else []:
        if not isinstance(msg, dict):
            continue
        if msg.get("read") is False:
            return "unread inbox message"
        ts = parse_ts(msg.get("timestamp"))
        if msg.get("from") == "team-lead" and ts and ts >= cutoff:
            return "lead messaged it recently"
    return None


def decide(session_id: str, session_dir: Path, reg: dict) -> list[dict]:
    team = team_for(session_id, session_dir, reg)
    if not team:
        return []
    try:
        config = json.loads((TEAMS / team / "config.json").read_text())
    except (OSError, ValueError):
        return []
    t = now()
    cutoff = t - IDLE_MIN * 60
    latest: dict[str, tuple[float, Path]] = {}
    for mtime, meta, data in metas(session_dir):
        if data.get("teamName") not in (None, team):
            continue
        name = data["name"]
        if name not in latest or mtime > latest[name][0]:
            latest[name] = (mtime, meta)
    keep = keep_names()
    workflows = running_workflow_names(session_dir)
    idle_marks = reg["idle_at"].get(team, {})
    lead_msgs = reg["lead_msg"].get(team, {})
    rows = []
    for member in config.get("members", []):
        name = member.get("name")
        if not name or name == "team-lead" or member.get("agentType") == "team-lead":
            continue
        meta = latest.get(name)
        if not meta:
            continue
        gone_at = reg.get("gone", {}).get(session_id, {}).get(name)
        if gone_at and meta[0] <= gone_at:
            continue
        agent_id = meta[1].name.removeprefix("agent-").removesuffix(".meta.json")
        if agent_id in reg.get("gone", {}).get(session_id, {}):
            continue
        row: dict = {"name": name, "agent_id": agent_id, "action": "keep", "why": "", "idle_min": None}
        state, last_ts = transcript_state(Path(str(meta[1]).replace(".meta.json", ".jsonl")))
        marked = idle_marks.get(name)
        if state == "unknown" and marked:
            state, last_ts = "idle", marked
        if marked and last_ts and marked > last_ts:
            state, last_ts = "idle", marked
        idle_for = (t - last_ts) / 60 if last_ts else None
        row["idle_min"] = idle_for
        msg_ts = lead_msgs.get(name)
        inbox = inbox_recent(team, name, cutoff)
        if state == "running":
            row["why"] = "running"
        elif state == "unknown":
            row["why"] = "no transcript found"
        elif name in keep:
            row["why"] = "keep list"
        elif name in workflows:
            row["why"] = "matches a running workflow"
        elif msg_ts and msg_ts >= cutoff:
            row["why"] = "lead messaged it recently"
        elif inbox:
            row["why"] = inbox
        elif idle_for is None or idle_for < IDLE_MIN:
            row["why"] = f"idle under {IDLE_MIN:g} min"
        else:
            row["action"] = "stop"
            row["why"] = f"idle {idle_for:.0f} min"
        rows.append(row)
    return rows


def read_payload() -> dict:
    try:
        data = json.load(sys.stdin)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def mode_mark(payload: dict) -> None:
    team = payload.get("team_name")
    name = payload.get("teammate_name")
    if not (team and name):
        agent_id = payload.get("agent_id")
        session_dir = session_dir_for(payload)
        if not (agent_id and session_dir):
            return
        meta = session_dir / "subagents" / f"agent-{agent_id}.meta.json"
        try:
            data = json.loads(meta.read_text())
        except (OSError, ValueError):
            return
        if data.get("taskKind") != "in_process_teammate":
            return
        team, name = data.get("teamName"), data.get("name")
        if not (team and name):
            return
    with Registry() as reg:
        reg.data["idle_at"].setdefault(team, {})[name] = now()
        reg.save()


def nudge_line(stops: list[dict]) -> str:
    names = ", ".join(" ".join(row["name"].split()) for row in stops)
    prefix = f"{len(stops)} idle agents older than {IDLE_MIN:g} min: "
    suffix = ". Stop them with TaskStop unless still needed."
    budget = 1024 - len((prefix + suffix).encode("utf-8"))
    if len(names.encode("utf-8")) > budget:
        names = names.encode("utf-8")[: budget - 3].decode("utf-8", "ignore") + "..."
    return prefix + names + suffix


def last_closeout_tokens(session_id: str, agent_id: str) -> int:
    path = HOME / ".claude" / "logs" / "dispatch.jsonl"
    try:
        with path.open("rb") as handle:
            position = handle.seek(0, os.SEEK_END)
            pending = b""
            while position:
                size = min(position, TAIL_BYTES)
                position -= size
                handle.seek(position)
                lines = (handle.read(size) + pending).split(b"\n")
                pending = lines.pop(0) if position else b""
                for raw in reversed(lines):
                    try:
                        entry = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(entry, dict):
                        continue
                    if (
                        entry.get("event") == "closeout"
                        and entry.get("session_id") == session_id
                        and entry.get("agent_id") == agent_id
                    ):
                        tokens = entry.get("tokens_in")
                        return tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else 0
    except OSError:
        pass
    return 0


def mode_post(payload: dict) -> None:
    if payload.get("agent_id"):
        return
    session_id = payload.get("session_id")
    session_dir = session_dir_for(payload)
    if not (session_id and session_dir):
        return
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}
    with Registry() as reg:
        if tool == "TaskStop" and "no task found" in json.dumps(payload.get("tool_response")).lower():
            task_id = tool_input.get("task_id") or tool_input.get("shell_id")
            if isinstance(task_id, str):
                names = {task_id}
                for _, meta, data in metas(session_dir):
                    if meta.name == f"agent-{task_id}.meta.json":
                        names.add(data["name"])
                gone = reg.data["gone"].setdefault(session_id, {})
                for name in names:
                    gone[name] = now()
        if tool == "SendMessage" and isinstance(tool_input.get("to"), str):
            team = team_for(session_id, session_dir, reg.data)
            if team:
                reg.data["lead_msg"].setdefault(team, {})[tool_input["to"]] = now()
        nudged = reg.data["nudged"].get(session_id)
        if not isinstance(nudged, dict):
            nudged = {}
        stops = [
            row
            for row in decide(session_id, session_dir, reg.data)
            if row["action"] == "stop" and row["agent_id"] not in nudged
        ]
        selected = []
        for row in stops:
            candidate = selected + [row]
            if len(nudge_line(candidate).encode("utf-8")) <= 1024:
                selected.append(row)
            if len(", ".join(" ".join(item["name"].split()) for item in candidate).encode("utf-8")) > 850:
                break
        stops = selected
        if stops and not DRY_RUN:
            reg.data["nudged"][session_id] = nudged
            for row in stops:
                nudged[row["agent_id"]] = now()
        reg.save()
    if not stops:
        return
    line = nudge_line(stops)
    log(f"{'DRY-RUN ' if DRY_RUN else ''}nudge session={session_id[:8]} {line}")
    if DRY_RUN:
        return
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": line}}))


def newest_orch_state() -> Path | None:
    files = sorted(ORCH_DIR.glob("ORCH-STATE-*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if files and now() - files[0].stat().st_mtime < 24 * 3600:
        return files[0]
    return None


def mode_stop(payload: dict) -> None:
    if payload.get("agent_id"):
        return
    session_id = payload.get("session_id")
    session_dir = session_dir_for(payload)
    target = newest_orch_state()
    if not (session_id and session_dir and target):
        return
    with Registry() as reg:
        stops = [row for row in decide(session_id, session_dir, reg.data) if row["action"] == "stop"]
        names = sorted(row["name"] for row in stops)
        key = f"{target.name}:{session_id}"
        signature = ",".join(names)
        if reg.data["orch_hash"].get(key, "") == signature:
            reg.save()
            return
        reg.data["orch_hash"][key] = signature
        reg.save()
    begin = f"<!-- idle-agents:{session_id[:8]} -->"
    end = f"<!-- /idle-agents:{session_id[:8]} -->"
    block = ""
    if names:
        block = f"{begin}\n## Idle agents (hook, {iso(now())}, session {session_id[:8]})\n{nudge_line(stops)}\n{end}\n"
    log(f"{'DRY-RUN ' if DRY_RUN else ''}orch-state {target.name} session={session_id[:8]} idle=[{signature}]")
    if DRY_RUN:
        return
    text = target.read_text()
    if begin in text and end in text:
        head, rest = text.split(begin, 1)
        tail = rest.split(end, 1)[1].lstrip("\n")
        head = head.rstrip("\n")
        text = head + ("\n\n" + block if block else "\n") + ("\n" + tail if tail else "")
    elif block:
        text = text.rstrip("\n") + "\n\n" + block
    else:
        return
    tmp = target.with_suffix(".md.tmp")
    tmp.write_text(text)
    os.replace(tmp, target)


def mode_scan(argv: list[str]) -> None:
    session_dir = None
    session_id = None
    if "--session-dir" in argv:
        session_dir = Path(argv[argv.index("--session-dir") + 1]).expanduser()
        session_id = session_dir.name
    elif "--session" in argv:
        session_id = argv[argv.index("--session") + 1]
        session_dir = session_dir_for({"session_id": session_id})
    if not (session_id and session_dir and session_dir.is_dir()):
        raise SystemExit("usage: idle-agents.py scan --session-dir <dir> | --session <id>")
    with Registry() as reg:
        rows = decide(session_id, session_dir, reg.data)
        team = reg.data["session_team"].get(session_id)
        reg.save()
    print(f"session {session_id}  team {team}  idle threshold {IDLE_MIN:g} min  dry-run {DRY_RUN}")
    for row in sorted(rows, key=lambda r: (r["action"] != "stop", r["name"])):
        idle = "-" if row["idle_min"] is None else f"{row['idle_min']:.0f}m"
        print(f"  {row['action']:<5} {row['name']:<28} {idle:>6}  {row['why']}")
    stops = [row for row in rows if row["action"] == "stop"]
    print(nudge_line(stops) if stops else "no idle agents past the threshold")
    for row in stops:
        log(f"DRY-RUN scan session={session_id[:8]} would-stop {row['name']} ({row['why']})")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "scan":
        mode_scan(sys.argv[2:])
        return
    payload = read_payload()
    try:
        {"mark": mode_mark, "post": mode_post, "stop": mode_stop}[mode](payload)
    except Exception as exc:
        log(f"error mode={mode}: {exc!r}")


if __name__ == "__main__":
    main()
