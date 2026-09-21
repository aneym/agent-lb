#!/usr/bin/env python3
"""PreToolUse guard on the Agent tool: protect Anthropic quota and record dispatches.

Opus, Sonnet, and Fable share the ``anthropic_top_thinking`` quota. Anthropic
volume dispatches require a fresh, healthy limit-watch snapshot; non-Anthropic
forwarders retain their existing behavior. ``fork`` is always allowed and recorded
with ``"fork": true``. Ledger I/O remains best-effort, but Anthropic quota-check
errors deny the dispatch.
"""

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FORWARDER_SEATS = {
    "astra", "implementer", "codex-verifier", "codex-test-runner",
    "computer-use", "cursor-seat",
}
ANTHROPIC_MODEL_MARKERS = ("opus", "sonnet", "fable", "haiku", "claude")
SNAPSHOT_MAX_AGE_SECONDS = 600
CLASS_TAG = re.compile(r"^\s*\[class:([a-z0-9_-]+)\]", re.IGNORECASE)
LEDGER = Path(os.environ.get("ROUTE_LEDGER") or os.environ.get("DISPATCH_LEDGER") or Path.home() / ".claude" / "logs" / "dispatch.jsonl")
# Same installed path the `route` CLI reads (ROUTE_TABLE): one table, two readers.
TABLE = Path(
    os.environ.get("ROUTING_TABLE")
    or os.environ.get("ROUTE_TABLE")
    or Path.home() / ".agent-lb" / "managed" / "coding-agents" / "routing-table.json"
)

REASON = (
    "seat-guard: {what}. Opus, Sonnet and Fable share anthropic_top_thinking. "
    "Route to a Codex or cursor seat via `route pick <class>`. "
    "Canon: ~/.agents/policy/coding-agents/ROUTING.md"
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def task_class(prompt: str, subagent: str):
    """`[class:x]` tag wins; else the routing table's chains name the class."""
    match = CLASS_TAG.match(prompt or "")
    if match:
        return match.group(1).lower()
    if not subagent:
        return None
    try:
        classes = json.loads(TABLE.read_text()).get("classes") or {}
    except Exception:
        return None
    fallback = None
    for name, spec in classes.items():
        if not isinstance(spec, dict):
            continue
        seats = [str(entry.get("seat") or "") for entry in (spec.get("chain") or []) if isinstance(entry, dict)]
        if seats and seats[0].lower() == subagent:
            return name
        if fallback is None and subagent in [seat.lower() for seat in seats]:
            fallback = name
    return fallback


def append(record: dict) -> None:
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
    except Exception:
        pass


def is_anthropic_seat(subagent: str, model: str) -> bool:
    """Return whether this dispatch consumes the shared Anthropic quota."""
    if any(marker in model for marker in ANTHROPIC_MODEL_MARKERS):
        return True
    return subagent not in FORWARDER_SEATS


def emit_denial(denial: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": REASON.format(what=denial),
    }}))


def anthropic_snapshot_denial(snapshot_path: Path) -> str | None:
    """Validate the limit-watch snapshot; callers deny on every guard error."""
    try:
        snapshot: Any = json.loads(snapshot_path.read_text())
    except FileNotFoundError:
        return "missing snapshot"
    except (OSError, json.JSONDecodeError):
        return "unparseable snapshot"
    try:
        if not isinstance(snapshot, dict):
            raise ValueError("snapshot is not an object")
        polled_at = str(snapshot["polled_at"])
        if polled_at.endswith("Z"):
            polled_at = polled_at[:-1] + "+00:00"
        observed_at = datetime.fromisoformat(polled_at)
        if observed_at.tzinfo is None:
            raise ValueError("polled_at is not timezone-aware")
        age_seconds = (datetime.now(timezone.utc) - observed_at.astimezone(timezone.utc)).total_seconds()
        if age_seconds > SNAPSHOT_MAX_AGE_SECONDS:
            return "stale snapshot"
        if snapshot["reachable"] is not True:
            return "snapshot reports unreachable"
        usable_count = snapshot["providers"]["anthropic"]["usable_count"]
        if not isinstance(usable_count, int):
            raise ValueError("usable_count is not an integer")
        if usable_count < 2:
            return "anthropic usable_count < 2"
        fable_eligible_usable = snapshot["fable_eligible_usable"]
        if not isinstance(fable_eligible_usable, int):
            raise ValueError("fable_eligible_usable is not an integer")
        if fable_eligible_usable < 2:
            return "fable_eligible_usable < 2"
    except Exception:
        return "internal error reading snapshot"
    return None


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        emit_denial("malformed Agent hook input")
        return
    if not isinstance(payload, dict) or payload.get("tool_name") != "Agent":
        return
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    subagent = str(tool_input.get("subagent_type") or "").strip().lower()
    model = str(tool_input.get("model") or "").strip().lower()
    prompt = str(tool_input.get("prompt") or "")

    record = {
        "ts": now(),
        "event": "dispatch",
        "session_id": payload.get("session_id"),
        "subagent_type": subagent or None,
        "model": model or None,
        "name": str(tool_input.get("name") or "") or None,
        "task_class": task_class(prompt, subagent),
        # The join key for the closeout: a subagent's transcript opens with this
        # exact prompt string, so the pair can be matched without guessing.
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "cwd": payload.get("cwd"),
    }

    if subagent == "fork":
        record["fork"] = True
        append(record)
        return

    denial = "this dispatch pins a Fable model on a subagent" if "fable" in model else None
    if denial is None and is_anthropic_seat(subagent, model):
        if os.environ.get("SEAT_GUARD_ALLOW_ANTHROPIC") == "1":
            record["anthropic_override"] = True
            record["reason"] = "SEAT_GUARD_ALLOW_ANTHROPIC=1 owner override"
        else:
            denial = anthropic_snapshot_denial(
                Path(os.environ.get("LIMIT_WATCH_SNAPSHOT") or Path.home() / ".agent-lb" / "state" / "limit-watch.json")
            )

    if denial:
        record["denied"] = True
        record["reason"] = denial
    append(record)
    if not denial:
        return
    emit_denial(denial)


if __name__ == "__main__":
    # The installed wrapper ends in `|| true`, so a crash would admit. Deny instead.
    try:
        main()
    except Exception:
        emit_denial("seat-guard crashed; failing closed")
