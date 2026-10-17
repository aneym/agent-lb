import fcntl
import hashlib
import json
import os
import sys
from pathlib import Path


def main() -> None:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict) or "agent_id" not in payload:
        return
    session = payload.get("session_id")
    agent = payload["agent_id"]
    if not isinstance(session, str) or not session or not isinstance(agent, str) or not agent:
        return
    budget = int(os.environ.get("SEAT_BUDGET_CALLS", "120"))
    if budget < 1:
        return
    root = Path(os.environ.get("SEAT_BUDGET_STATE_DIR")
                or "~/.agent-rails/factory/hooks/seat-budget").expanduser()
    root.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(json.dumps([session, agent]).encode()).hexdigest()
    descriptor = os.open(root / (key + ".json"), os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "r+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        raw = stream.read()
        count = (int(json.loads(raw)) if raw else 0) + 1
        stream.seek(0)
        json.dump(count, stream)
        stream.truncate()
    if count >= budget and (count - budget) % 40 == 0:
        text = (f"seat-budget: {count} tool calls in this seat; return your HANDOFF now "
                "(done, unverified, next slice) and let the lead re-dispatch the rest.")
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
