#!/usr/bin/env python3
"""Fail-closed Agent PreToolUse guard.

The installed wrapper supplies a shell-level denial fallback. Failures handled
inside this file still emit denials with their specific reason.
"""

import sys

FALLBACK_DENIAL = (
    '{"hookSpecificOutput":{"hookEventName":"PreToolUse",'
    '"permissionDecision":"deny",'
    '"permissionDecisionReason":"seat-guard crashed; failing closed"}}'
)


def emit_fallback_denial():
    print(FALLBACK_DENIAL)

def emit_denial(denial):
    reason = "seat-guard: " + denial
    if "REASON" in globals():
        reason = REASON.format(what=denial)
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))


try:
    import hashlib
    import json
    import os
    import re
    from datetime import datetime, timezone
    from pathlib import Path

    FORWARDER_SEATS = {"astra", "implementer", "codex-verifier", "codex-test-runner", "computer-use", "cursor-seat"}
    ANTHROPIC_MODEL_MARKERS = ("opus", "sonnet", "fable", "haiku", "claude")
    SNAPSHOT_MAX_AGE_SECONDS = 600
    SNAPSHOT_MAX_FUTURE_SECONDS = 60
    CLASS_TAG = re.compile(r"^\s*\[class:([a-z0-9_-]+)\]", re.IGNORECASE)
    LEDGER = Path(os.environ.get("ROUTE_LEDGER") or os.environ.get("DISPATCH_LEDGER") or Path.home() / ".claude" / "logs" / "dispatch.jsonl")
    TABLE = Path(os.environ.get("ROUTING_TABLE") or os.environ.get("ROUTE_TABLE") or Path.home() / ".agent-lb" / "managed" / "coding-agents" / "routing-table.json")
    REASON = (
        "seat-guard: {what}. Opus, Sonnet and Fable share anthropic_top_thinking. "
        "Route to a Codex or cursor seat via `route pick <class>`. "
        "Canon: ~/.agents/policy/coding-agents/ROUTING.md"
    )

    def now():
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    def task_class(prompt, subagent):
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

    def append(record):
        try:
            LEDGER.parent.mkdir(parents=True, exist_ok=True)
            with LEDGER.open("a") as handle:
                handle.write(json.dumps(record, separators=(",", ":")) + "\n")
        except Exception:
            return False
        return True

    def is_anthropic_model(model):
        return any(marker in model for marker in ANTHROPIC_MODEL_MARKERS)

    def is_anthropic_seat(subagent, model):
        return is_anthropic_model(model) or subagent not in FORWARDER_SEATS

    def anthropic_snapshot_denial(snapshot_path):
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
            if polled_at.endswith("Z"):
                polled_at = polled_at[:-1] + "+00:00"
            observed_at = datetime.fromisoformat(polled_at)
            if observed_at.tzinfo is None:
                raise ValueError("polled_at is not timezone-aware")
            age_seconds = (datetime.now(timezone.utc) - observed_at.astimezone(timezone.utc)).total_seconds()
            if age_seconds < -SNAPSHOT_MAX_FUTURE_SECONDS:
                return "snapshot dated in the future"
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

    def main():
        try:
            payload = json.load(sys.stdin)
        except Exception:
            emit_denial("malformed Agent hook input")
            return
        if not isinstance(payload, dict):
            emit_denial("invalid Agent hook input")
            return
        tool_name = payload.get("tool_name")
        if isinstance(tool_name, str) and tool_name != "Agent":
            return
        if tool_name != "Agent":
            emit_denial("invalid Agent hook input")
            return
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            emit_denial("invalid Agent hook input")
            return
        for field in ("subagent_type", "model"):
            if field in tool_input and not isinstance(tool_input[field], str):
                emit_denial("invalid Agent hook input")
                return
        subagent = (tool_input.get("subagent_type") or "").strip().lower()
        model = (tool_input.get("model") or "").strip().lower()
        prompt = str(tool_input.get("prompt") or "")
        record = {"ts": now(), "event": "dispatch", "session_id": payload.get("session_id"), "subagent_type": subagent or None, "model": model or None, "name": str(tool_input.get("name") or "") or None, "task_class": task_class(prompt, subagent), "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(), "cwd": payload.get("cwd")}
        denial = "this dispatch pins a Fable model on a subagent" if "fable" in model else None
        is_fork = subagent == "fork"
        if is_fork:
            record["fork"] = True
        requires_snapshot = is_anthropic_seat(subagent, model) and (not is_fork or is_anthropic_model(model))
        if denial is None and requires_snapshot:
            if os.environ.get("SEAT_GUARD_ALLOW_ANTHROPIC") == "1":
                record["anthropic_override"] = True
                record["reason"] = "SEAT_GUARD_ALLOW_ANTHROPIC=1 owner override"
                if not append(record):
                    emit_denial("could not record Anthropic override")
                return
            denial = anthropic_snapshot_denial(Path(os.environ.get("LIMIT_WATCH_SNAPSHOT") or Path.home() / ".agent-lb" / "state" / "limit-watch.json"))
        if denial:
            record["denied"] = True
            record["reason"] = denial
        append(record)
        if denial:
            emit_denial(denial)

    main()
except Exception:
    emit_fallback_denial()
