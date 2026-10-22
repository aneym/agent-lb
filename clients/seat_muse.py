from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from clients.seat_constants import MUSE_STALL_SECONDS


def post_stall(vendor: str, cwd: Path, error: str) -> None:
    owner = os.environ.get("SEAT_OWNER_PANE") or os.environ.get("FACTORY_OWNER_PANE")
    if not owner or owner == "none":
        print("seat: STALLED with no owner pane to tell (set SEAT_OWNER_PANE)", file=sys.stderr)
        return
    poster = os.environ.get("SEAT_LANE_POST") or str(Path.home() / ".agent-rails/factory-runtime/bin/lane-post")
    sender = os.environ.get("HERDR_PANE_ID") or owner
    text = f"{vendor} seat stalled in {cwd}: {error}; the run was stopped and failed. Relaunch or reassign it."
    try:
        completed = subprocess.run([poster, "post", "--to", owner, "--from", sender, "--kind", "task", text],
                                   capture_output=True, text=True, timeout=30, check=False)
        print(f"seat: stall bulletin to {owner} rc={completed.returncode}", file=sys.stderr)
    except (OSError, subprocess.TimeoutExpired) as error_:
        print(f"seat: stall bulletin to {owner} failed: {error_}", file=sys.stderr)


def muse_call(call: dict[str, Any]) -> str:
    name = str(call.get("name") or "tool")
    try:
        args = json.loads(call.get("args") or "{}")
    except ValueError:
        args = {}
    first = next((v for v in args.values() if isinstance(v, str)), "") if isinstance(args, dict) else ""
    return f"{name} {' '.join(first.split())[:120]}".rstrip()


def muse_progress(line: str) -> str | None:
    try:
        event = json.loads(line)
    except ValueError:
        return None
    payload = event.get("payload") if isinstance(event, dict) else None
    if not isinstance(payload, dict):
        return None
    inner = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    kind = inner.get("kind")
    if kind == "reasoning_summary_committed" and inner.get("text"):
        return "muse: " + " ".join(str(inner["text"]).split())[:200]
    if kind == "assistant_tool_calls_committed":
        calls = [muse_call(call) for call in inner.get("tool_calls") or [] if isinstance(call, dict)]
        return "muse: " + "; ".join(calls)[:240] if calls else None
    if kind == "todo_snapshot_updated":
        items = [i for i in inner.get("items") or [] if isinstance(i, dict)]
        active = next((i.get("text") for i in items if i.get("status") == "in_progress"), None)
        done = sum(1 for i in items if i.get("status") == "completed")
        return f"muse: todo {done}/{len(items)} done" + (f", now: {active}"[:160] if active else "")
    if kind == "status" and inner.get("message") and not re.search(r"attempt 1/|serialized", str(inner["message"])):
        return "muse: " + str(inner["message"])[:200]
    if payload.get("terminal"):
        return f"muse: run {payload['terminal']}"
    return None


class MuseTail:
    def __init__(self) -> None:
        self.started = False
        self.done = threading.Event()
        self.last = time.monotonic()
        self.stall_limit = float(os.environ.get("SEAT_STALL_SECONDS") or MUSE_STALL_SECONDS)

    def idle(self) -> float:
        return time.monotonic() - self.last

    def __call__(self, line: str) -> str | None:
        self.last = time.monotonic()
        if not self.started:
            try:
                stream = json.loads(line).get("stream") or {}
            except (ValueError, AttributeError):
                stream = {}
            if stream.get("kind") == "session" and stream.get("id"):
                self.started = True
                threading.Thread(target=self.follow, args=(str(stream["id"]),), daemon=True).start()
        shown = muse_progress(line)
        return shown if shown and (not self.started or "run " in shown) else None

    def follow(self, session_id: str) -> None:
        root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "muse" / "sessions"
        path = None
        while path is None and not self.done.wait(0.5):
            path = next(iter(root.glob(f"*/*/*/{session_id}/session.jsonl")), None)
        if path is None:
            return
        with path.open(encoding="utf-8", errors="replace") as stream:
            pending = ""
            while True:
                chunk = stream.readline()
                if chunk:
                    self.last = time.monotonic()
                    pending += chunk
                    if not pending.endswith("\n"):
                        continue
                    shown = muse_progress(pending)
                    pending = ""
                    if shown and "run " not in shown:
                        print(shown, file=sys.stderr, flush=True)
                elif self.done.wait(0.5):
                    return

    def close(self) -> None:
        self.done.set()
