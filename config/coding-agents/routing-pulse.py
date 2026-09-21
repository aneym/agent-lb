#!/usr/bin/env python3
"""Nudge sessions spending shared Anthropic capacity on volume work.

Count Fable, Opus and Sonnet; report missing analytics explicitly.
"""

import json
import math
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

LB = os.environ.get("AGENT_LB_URL") or "http://127.0.0.1:2455"
CANON = "~/.agents/policy/coding-agents/ROUTING.md"
LEDGER = Path(os.environ.get("ROUTE_LEDGER") or os.environ.get("DISPATCH_LEDGER") or Path.home() / ".claude" / "logs" / "dispatch.jsonl")
DRIVER_MODELS = ("fable", "opus", "sonnet")
BURST_MINUTES = 60
BURST_MIN_REQUESTS = 40
RATIO_MINUTES = 360
RATIO_MIN_REQUESTS = 25
CLOSEOUT_RATIO_FLOOR = 0.25
THROTTLE_SECONDS = 900


def anthropic_requests(session_id: str, window_minutes: int):
    url = f"{LB}/api/sessions/{session_id}/analytics?windowMinutes={window_minutes}"
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            data = json.load(response)
    except Exception:
        return None, 0.0
    if not isinstance(data, dict) or not isinstance(data.get("seats"), list):
        return None, 0.0
    requests = cost = 0.0
    try:
        for seat in data["seats"]:
            model = str(seat.get("model") or "").lower()
            if any(driver in model for driver in DRIVER_MODELS):
                count = seat.get("requests")
                if (
                    isinstance(count, bool) or not isinstance(count, (int, float))
                    or not math.isfinite(count) or count < 0
                ):
                    return None, 0.0
                requests += count
                cost += float(seat.get("costUsd") or 0)
    except (AttributeError, TypeError, ValueError):
        return None, 0.0
    if not math.isfinite(cost):
        return None, 0.0
    return requests, cost


def closeouts(session_id: str, window_minutes: int) -> int:
    floor = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    count = 0
    try:
        with LEDGER.open() as handle:
            lines = handle.readlines()[-4000:]
    except Exception:
        return 0
    for line in lines:
        try:
            record = json.loads(line)
            if record.get("event") != "closeout" or record.get("session_id") != session_id:
                continue
            if datetime.fromisoformat(str(record.get("ts")).replace("Z", "+00:00")) >= floor:
                count += 1
        except Exception:
            continue
    return count


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    event = payload.get("hook_event_name") or "UserPromptSubmit"
    session_id = os.environ.get("ROUTING_PULSE_SESSION_ID") or payload.get("session_id")
    if not isinstance(session_id, str) or len(session_id) < 8:
        return

    # Throttle on a COMPLETED check, not only on a firing one: a quiet session
    # would otherwise re-hit the LB over HTTP on every prompt.
    throttle = Path.home() / ".cache" / "routing-pulse" / session_id
    try:
        if throttle.exists() and time.time() - throttle.stat().st_mtime < THROTTLE_SECONDS:
            return
    except OSError:
        return

    burst, _ = anthropic_requests(session_id, BURST_MINUTES)
    if burst is None:
        print("ROUTING PULSE: telemetry missing (session analytics unavailable)")
        return
    window, cost = anthropic_requests(session_id, RATIO_MINUTES)
    if window is None:
        print("ROUTING PULSE: telemetry missing (session analytics unavailable)")
        return
    try:
        throttle.parent.mkdir(parents=True, exist_ok=True)
        throttle.touch()
    except OSError:
        pass

    closed = closeouts(session_id, RATIO_MINUTES)
    is_burst = burst >= BURST_MIN_REQUESTS
    is_ratio = (window or 0) >= RATIO_MIN_REQUESTS and closed < CLOSEOUT_RATIO_FLOOR * (window or 0)
    if not (is_burst or is_ratio):
        return

    trigger = (
        f"{int(burst)} Anthropic requests in the last hour"
        if is_burst
        else f"{int(window or 0)} Anthropic requests in {RATIO_MINUTES // 60}h against {closed} seat closeouts"
    )
    context = (
        f"ROUTING PULSE (agent-lb session map): {trigger} (${cost:.2f}). Fable is "
        f"the binding pool and this session is the driver, so volume work here "
        f"costs the one thing that runs out. Opus 5 and Sonnet draw from the SAME "
        f"Anthropic quota as Fable (measured 2026-09-20: `anthropic_top_thinking` "
        f"429s name claude-opus-5, claude-sonnet-4-6 and claude-fable-5-1), so an "
        f"Opus seat is NOT free — it spends the pool the driver needs. Volume work "
        f"goes to a non-Anthropic seat: Codex (implementer/astra/codex-verifier), "
        f"cursor-seat, Kimi or GLM. Ask the router — `route pick <class>` (explore | "
        f"implement | mechanical | verify | review | computer) — and dispatch that "
        f"seat instead of doing the work yourself. Canon: {CANON}. Live numbers: "
        f"{LB}/s/{session_id[:8]}"
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": event,
        "additionalContext": context,
    }}))


if __name__ == "__main__":
    main()
