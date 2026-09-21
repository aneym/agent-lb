#!/usr/bin/env python3
"""Advisory Agent PreToolUse routing telemetry.

This hook never controls admission: model and capacity state are surfaced as
status so the caller can choose deliberately.
"""

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

FORWARDER_SEATS = {"astra", "implementer", "codex-verifier", "codex-test-runner", "computer-use", "cursor-seat"}
ANTHROPIC_MODEL_MARKERS = ("opus", "sonnet", "fable", "haiku", "claude")
SNAPSHOT_MAX_AGE_SECONDS = 600
SNAPSHOT_MAX_FUTURE_SECONDS = 60
CLASS_TAG = re.compile(r"^\s*\[class:([a-z0-9_-]+)\]", re.IGNORECASE)


def emit_advisory(advisory: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": (
                        "seat-guard advisory: " + advisory + ". "
                        "Capacity is status, not admission control. Check "
                        "`agent-lb status --provider anthropic --model claude-fable-5-1 --json`."
                    ),
                }
            }
        )
    )


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def task_class(prompt: str, subagent: str, table: Path) -> str | None:
    match = CLASS_TAG.match(prompt or "")
    if match:
        return match.group(1).lower()
    if not subagent:
        return None
    try:
        classes = json.loads(table.read_text()).get("classes") or {}
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


def append(record: dict, ledger: Path) -> bool:
    try:
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
    except Exception:
        return False
    return True


def is_anthropic_model(model: str) -> bool:
    return any(marker in model for marker in ANTHROPIC_MODEL_MARKERS)


def snapshot_advisory(snapshot_path: Path) -> str | None:
    try:
        snapshot = json.loads(snapshot_path.read_text())
    except FileNotFoundError:
        return "missing snapshot"
    except (OSError, json.JSONDecodeError):
        return "unparseable snapshot"
    try:
        if not isinstance(snapshot, dict):
            raise ValueError("snapshot is not an object")
        polled_at = str(snapshot["polled_at"])
        observed_at = datetime.fromisoformat(polled_at[:-1] + "+00:00" if polled_at.endswith("Z") else polled_at)
        if observed_at.tzinfo is None:
            raise ValueError("polled_at is not timezone-aware")
        age_seconds = (datetime.now(timezone.utc) - observed_at.astimezone(timezone.utc)).total_seconds()
        if age_seconds < -SNAPSHOT_MAX_FUTURE_SECONDS:
            return "snapshot dated in the future"
        if age_seconds > SNAPSHOT_MAX_AGE_SECONDS:
            return "stale snapshot"
        if snapshot["reachable"] is not True:
            return "snapshot reports unreachable"
        if snapshot["providers"]["anthropic"]["usable_count"] < 2:
            return "anthropic usable_count < 2"
        if snapshot["fable_eligible_usable"] < 2:
            return "fable_eligible_usable < 2"
    except Exception:
        return "internal error reading snapshot"
    return None


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        emit_advisory("malformed Agent hook input; dispatch was not blocked")
        return
    if not isinstance(payload, dict) or payload.get("tool_name") != "Agent":
        emit_advisory("invalid Agent hook input; dispatch was not blocked")
        return
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict) or any(
        field in tool_input and not isinstance(tool_input[field], str) for field in ("subagent_type", "model")
    ):
        emit_advisory("invalid Agent hook input; dispatch was not blocked")
        return
    subagent = (tool_input.get("subagent_type") or "").strip().lower()
    model = (tool_input.get("model") or "").strip().lower()
    prompt = str(tool_input.get("prompt") or "")
    ledger = Path(
        os.environ.get("ROUTE_LEDGER")
        or os.environ.get("DISPATCH_LEDGER")
        or Path.home() / ".claude" / "logs" / "dispatch.jsonl"
    )
    table = Path(
        os.environ.get("ROUTING_TABLE")
        or os.environ.get("ROUTE_TABLE")
        or Path.home() / ".agent-lb" / "managed" / "coding-agents" / "routing-table.json"
    )
    record = {
        "ts": now(),
        "event": "dispatch",
        "session_id": payload.get("session_id"),
        "subagent_type": subagent or None,
        "model": model or None,
        "name": str(tool_input.get("name") or "") or None,
        "task_class": task_class(prompt, subagent, table),
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "cwd": payload.get("cwd"),
    }
    advisories: list[str] = []
    if "fable" in model:
        record["model_advisory"] = "this dispatch pins a Fable model on a subagent"
        advisories.append(record["model_advisory"])
    is_fork = subagent == "fork"
    if is_fork:
        record["fork"] = True
    requires_snapshot = (is_anthropic_model(model) or subagent not in FORWARDER_SEATS) and (
        not is_fork or is_anthropic_model(model)
    )
    if requires_snapshot and os.environ.get("SEAT_GUARD_ALLOW_ANTHROPIC") != "1":
        capacity_advisory = snapshot_advisory(
            Path(os.environ.get("LIMIT_WATCH_SNAPSHOT") or Path.home() / ".agent-lb" / "state" / "limit-watch.json")
        )
        if capacity_advisory:
            record["capacity_advisory"] = capacity_advisory
            advisories.append(capacity_advisory)
    elif requires_snapshot:
        record["anthropic_override"] = True
    if not append(record, ledger):
        emit_advisory("could not record routing telemetry; dispatch was not blocked")
    elif advisories:
        emit_advisory("; ".join(advisories))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        emit_advisory("routing telemetry unavailable; dispatch was not blocked")
