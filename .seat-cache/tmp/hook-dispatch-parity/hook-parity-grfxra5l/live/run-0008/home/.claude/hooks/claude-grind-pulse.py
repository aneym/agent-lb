#!/usr/bin/env python3
import json
import os
import sys
import time


def main():
    payload = json.load(sys.stdin)
    session = payload.get("session_id")
    if (
        not isinstance(session, str)
        or not 1 <= len(session) <= 200
        or not all(char.isascii() and (char.isalnum() or char in "_-") for char in session)
    ):
        return
    now = time.time()
    cache_dir = os.path.expanduser("~/.cache/claude-grind-pulse")
    checked_file = os.path.join(cache_dir, session)
    if os.path.exists(checked_file) and now - os.stat(checked_file).st_mtime < 600:
        return

    import math
    import signal
    import urllib.parse
    import urllib.request
    from collections import deque
    from datetime import datetime, timezone
    from pathlib import Path

    signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, 2)
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    Path(checked_file).touch()
    fired = cache / f"{session}.fired"
    if fired.exists() and now - fired.stat().st_mtime < 7200:
        return
    base = os.environ.get("AGENT_LB_URL") or "http://127.0.0.1:2455"
    url = f"{base.rstrip('/')}/api/sessions/{urllib.parse.quote(session)}/analytics?windowMinutes=60"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=2) as response:
        analytics = json.load(response)
    claude = other = cost = 0.0
    for seat in analytics["seats"]:
        model = str(seat["model"]).lower()
        requests = float(seat["requests"])
        dollars = float(seat["costUsd"])
        if not all(math.isfinite(value) and value >= 0 for value in (requests, dollars)):
            return
        if any(name in model for name in ("claude", "opus", "fable", "sonnet")):
            claude += requests
            cost += dollars
        else:
            other += requests
    if claude < 120:
        return
    ledger = Path(os.environ.get("DISPATCH_LEDGER") or Path.home() / ".claude/logs/dispatch.jsonl")
    dispatches = 0
    with ledger.open() as handle:
        lines = deque(handle, maxlen=4000)
    for line in lines:
        record = json.loads(line)
        if record.get("event") != "dispatch" or record.get("session_id") != session:
            continue
        stamp = datetime.fromisoformat(record["ts"].replace("Z", "+00:00"))
        age = now - stamp.astimezone(timezone.utc).timestamp()
        if 0 <= age <= 3600:
            dispatches += 1
    if dispatches and other / claude >= 0.1:
        return
    text = (
        f"Last 60m: Claude {claude:.0f} requests (${cost:.2f}), non-Claude {other:.0f}, "
        f"dispatches {dispatches}. "
        "hands work (implementation, probes, sweeps, test runs, bulletin draining, landing, research) "
        "goes to gpt-implementer / gpt-explorer / codex-sol; Opus keeps plans, specs, verdicts. "
        "Use /route-fix to correct routing."
    )
    if len(text.encode()) > 600:
        return
    fired.touch()
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}))


def timeout(signum, frame):
    raise TimeoutError


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
