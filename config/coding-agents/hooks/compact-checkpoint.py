from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

TAIL_BYTES = 512 * 1024
LIMIT = 1024
MAX_BASH = 8
MAX_IDS = 12
TERMINAL = {"completed", "failed", "killed", "stopped", "cancelled", "error"}
INTERPRETER = re.compile(
    r"(?:^|(?<=[\s;&|(]))((?:[\w.~${}+-]*/)*(?:python(?:\d+(?:\.\d+)*)?|pytest|uv|node|bun|deno|npx|npm|pnpm"
    r"|ruby|perl|go|cargo))(?=[\s;&|)]|$)"
)
LEADING_CD = re.compile(r'^\s*cd\s+("[^"]+"|\'[^\']+\'|[^\s;&|]+)\s*(?:&&|;)')
LONG_QUOTE = re.compile(r'"[^"\n]{60,}"|\'[^\'\n]{60,}\'')
BACKGROUND_ID = re.compile(r"running in background with ID:?\s*([A-Za-z0-9_-]+)")
AGENT_ID = re.compile(r'agentId:?\s*"?([A-Za-z0-9_-]{6,})')
NOTIFY = re.compile(r"<task-notification>(.*?)</task-notification>", re.S)
ASK_PATTERNS = (
    re.compile(r"\b(ub_[a-z0-9]{6,})\b"),
    re.compile(r"\b(held-[0-9a-f]{8})\b"),
    re.compile(r'\bask[_ -]?id["\']?\s*[:=]\s*["\']?([A-Za-z0-9_-]{6,})'),
)
APPROVAL_PATTERNS = (
    re.compile(r"\b(alex-[a-z0-9][a-z0-9-]{5,})\b"),
    re.compile(r'\bapproval[_ -]?(?:record[_ -]?)?id["\']?\s*[:=]\s*["\']?([A-Za-z0-9_.-]{6,})'),
)
GRANT_PATTERNS = (
    re.compile(r"\b(g-[a-z0-9-]+-r\d+-[0-9a-f]{12})\b"),
    re.compile(r"AUTHORITY:\s*([A-Za-z0-9_.-]{4,})"),
)


def load_context():
    spec = importlib.util.spec_from_file_location("factory_context", Path(__file__).with_name("factory-context.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tail_records(path: Path) -> tuple:
    with path.open("rb") as stream:
        size = stream.seek(0, 2)
        start = max(0, size - TAIL_BYTES)
        stream.seek(start)
        if start:
            stream.readline()
        raw = stream.read(size - stream.tell())
    records = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            records.append((row, line))
    return records, size, start


def text_of(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(text_of(item) for item in value)
    if isinstance(value, dict):
        if value.get("type") == "text":
            return str(value.get("text", ""))
        return json.dumps(value)
    return "" if value is None else str(value)


def safe_command(command: str) -> str:
    first = command.split("\n", 1)[0]
    first = first.split("<<", 1)[0].rstrip()
    first = LONG_QUOTE.sub('"[...]"', first)
    return first[:300]


def interpreter_of(command: str, cwd: str) -> tuple:
    match = INTERPRETER.search(command)
    if not match:
        return None, None
    found = match.group(1)
    base = cwd
    cd = LEADING_CD.match(command)
    if cd:
        target = cd.group(1).strip("\"'")
        base = target if target.startswith("/") else os.path.normpath(os.path.join(cwd or "/", target))
    if "/" in found and not found.startswith(("/", "~", "$")) and base:
        return found, os.path.normpath(os.path.join(base, found))
    return found, found if found.startswith("/") else None


def add_ids(bucket: dict, patterns: tuple, text: str, source: str) -> None:
    for pattern in patterns:
        for match in pattern.finditer(text):
            bucket.setdefault(match.group(1), source)


def scan(records: list) -> dict:
    uses = {}
    order = []
    results = {}
    notified = {}
    asks, approvals, grants = {}, {}, {}
    for row, line in records:
        for match in NOTIFY.finditer(line.replace("\\n", "\n")):
            body = match.group(1)
            task = re.search(r"<task-id>([^<]+)</task-id>", body)
            tool = re.search(r"<tool-use-id>([^<]+)</tool-use-id>", body)
            status = re.search(r"<status>([^<]+)</status>", body)
            state = status.group(1).strip() if status else ""
            for key in (task.group(1).strip() if task else None, tool.group(1).strip() if tool else None):
                if key:
                    notified[key] = state
        message = row.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
                uses[block.get("id")] = {"name": block.get("name"), "input": tool_input, "cwd": row.get("cwd") or ""}
                order.append(block.get("id"))
                text = json.dumps(tool_input)
                add_ids(asks, ASK_PATTERNS, text, "transcript tool input")
                add_ids(approvals, APPROVAL_PATTERNS, text, "transcript tool input")
                add_ids(grants, GRANT_PATTERNS, text, "transcript tool input")
            elif block.get("type") == "tool_result":
                text = text_of(block.get("content"))
                extra = row.get("toolUseResult")
                results[block.get("tool_use_id")] = (text, extra if isinstance(extra, dict) else {})
                add_ids(asks, ASK_PATTERNS, text, "transcript tool result")
                add_ids(approvals, APPROVAL_PATTERNS, text, "transcript tool result")
                add_ids(grants, GRANT_PATTERNS, text, "transcript tool result")
    bash, tasks, agents = [], [], []
    stopped = {
        str(uses[key]["input"].get("task_id") or uses[key]["input"].get("shell_id") or "")
        for key in order
        if uses[key]["name"] in ("TaskStop", "KillShell", "KillBash")
    }
    for key in order:
        use = uses[key]
        name, tool_input, cwd = use["name"], use["input"], use["cwd"]
        text, extra = results.get(key, ("", {}))
        task_id = extra.get("backgroundTaskId") or extra.get("taskId")
        if not task_id:
            found = BACKGROUND_ID.search(text)
            task_id = found.group(1) if found else None
        if task_id and notified.get(task_id, notified.get(key, "")) not in TERMINAL and task_id not in stopped:
            tasks.append(
                {
                    "task_id": task_id,
                    "tool": name,
                    "tool_use_id": key,
                    "source": "transcript tool_result without a terminal task-notification",
                }
            )
        if name == "Bash" and isinstance(tool_input.get("command"), str):
            command = tool_input["command"]
            shown = safe_command(command)
            interpreter, absolute = interpreter_of(shown, cwd)
            cd = LEADING_CD.match(shown)
            entry = {
                "command": shown,
                "cwd": cwd,
                "cd": cd.group(1).strip("\"'") if cd else None,
                "interpreter": interpreter,
                "interpreter_path": absolute,
                "background": bool(tool_input.get("run_in_background")),
                "task_id": task_id,
                "status": "pending" if key not in results else "returned",
                "source": "transcript Bash tool_use",
            }
            bash.append(entry)
        if name in ("Agent", "Task"):
            agent_id = extra.get("agentId")
            if not agent_id:
                found = AGENT_ID.search(text)
                agent_id = found.group(1) if found else None
            launched = key in results and (
                extra.get("status") in ("async_launched", "launched")
                or "async agent launched" in text.lower()
                or bool(tool_input.get("run_in_background"))
            )
            closed = (
                (key in results and not launched)
                or notified.get(key) in TERMINAL
                or (agent_id and notified.get(agent_id) in TERMINAL)
            )
            if not closed:
                agents.append(
                    {
                        "name": tool_input.get("name"),
                        "subagent_type": tool_input.get("subagent_type"),
                        "description": str(tool_input.get("description") or "")[:80],
                        "agent_id": agent_id,
                        "tool_use_id": key,
                        "source": "transcript Agent tool_use without a closeout",
                    }
                )
    return {
        "bash": bash[-MAX_BASH:],
        "background_tasks": tasks[-MAX_IDS:],
        "agents": agents[-MAX_IDS:],
        "ask_ids": [{"id": key, "source": value} for key, value in list(asks.items())[-MAX_IDS:]],
        "approval_ids": [{"id": key, "source": value} for key, value in list(approvals.items())[-MAX_IDS:]],
        "grant_ids": [{"id": key, "source": value} for key, value in list(grants.items())[-MAX_IDS:]],
    }


def git_head(cwd: str):
    if not cwd or not Path(cwd).is_dir():
        return None
    try:
        result = subprocess.run(["git", "-C", cwd, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def build(payload: dict, context) -> tuple:
    lanes = Path(os.environ.get("FACTORY_LANES_DIR") or "~/.agent-rails/lanes").expanduser()
    agents = Path(os.environ.get("FACTORY_AGENTS_DIR") or "~/.agent-rails/agents").expanduser()
    thread_id, host = context.t3_selection(payload)
    lane, agent, _ = context.identity(payload, lanes, agents, thread_id, host)
    session = str(payload.get("session_id") or "")
    path, owner = context.checkpoint_path(lanes, agents, lane, agent, session)
    cwd = str(payload.get("cwd") or os.getcwd())
    checkpoint = {
        "schema": "compact-checkpoint.v1",
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session_id": session,
        "trigger": payload.get("trigger"),
        "owner": owner,
        "cwd": cwd,
        "git_head": {"value": git_head(cwd), "source": "git rev-parse HEAD in cwd"},
        "rule": "pointers only; approval ids are references, never consent",
    }
    if owner.get("kind") == "lane":
        state = Path(owner["dir"]) / "STATE.md"
        if state.is_file():
            checkpoint["state_md"] = {
                "path": str(state),
                "mtime": datetime.fromtimestamp(state.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
                "source": "stat",
            }
    transcript = Path(str(payload.get("transcript_path") or "")).expanduser()
    if payload.get("transcript_path") and transcript.is_file():
        records, size, start = tail_records(transcript)
        checkpoint["transcript"] = {
            "path": str(transcript),
            "offset": size,
            "read_from": start,
            "source": "hook stdin transcript_path, bounded tail read",
        }
        checkpoint.update(scan(records))
    else:
        checkpoint["transcript"] = {
            "path": str(transcript) if payload.get("transcript_path") else None,
            "offset": None,
            "source": "transcript unreadable",
        }
    return path, checkpoint


def write(path: Path, checkpoint: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(checkpoint, stream, indent=1)
    os.replace(temporary, path)


def instructions(path, checkpoint) -> str:
    lines = [
        "Compaction instructions: keep these verbatim in the summary, never paraphrased: exact interpreter paths "
        "and commands with their cwd; task and background task ids; seat and agent names and ids; grant ids; "
        "ask ids and approval record ids as references only, never as consent; and the next action.",
    ]
    if path is not None:
        lines.append("Checkpoint: {} (reconcile before new work).".format(path))
    facts = []
    for entry in (checkpoint or {}).get("bash", [])[-2:]:
        facts.append("cmd in {}: {}".format(entry["cwd"] or "-", entry["command"][:160]))
    for entry in (checkpoint or {}).get("background_tasks", []):
        facts.append("running task {}".format(entry["task_id"]))
    for entry in (checkpoint or {}).get("agents", []):
        facts.append(
            "open seat {} {}".format(
                entry.get("name") or entry.get("subagent_type") or "-", entry.get("agent_id") or entry["tool_use_id"]
            )
        )
    for key, label in (("ask_ids", "ask"), ("approval_ids", "approval ref"), ("grant_ids", "grant")):
        for entry in (checkpoint or {}).get(key, [])[-4:]:
            facts.append("{} {}".format(label, entry["id"]))
    for fact in facts:
        candidate = "\n".join(lines + ["- " + fact])
        if len(candidate.encode()) > LIMIT:
            break
        lines.append("- " + fact)
    return "\n".join(lines)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    path, checkpoint = None, None
    try:
        path, checkpoint = build(payload, load_context())
        write(path, checkpoint)
    except Exception as exc:
        print("compact-checkpoint: not written ({})".format(type(exc).__name__), file=sys.stderr)
        path = None
    text = instructions(path, checkpoint)
    sys.stdout.write(text.encode()[:LIMIT].decode("utf-8", errors="ignore") + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
