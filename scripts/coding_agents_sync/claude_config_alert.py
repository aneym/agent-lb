import json
import os
import subprocess
import sys
from pathlib import Path

STATE = Path(".agent-lb/claude-config-alert.json")
BLOCKING = ("dirty", "diverged", "off_main", "failed")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from app.core.thread_delivery import send

ALERT_TO = "factory-office"


def notify(home, outcome, kept):
    state_path = home / STATE
    if outcome["result"] in BLOCKING:
        signature = [outcome["result"], outcome.get("detail", "")]
    elif kept:
        signature = ["dirty", ", ".join(kept[:20])]
    else:
        signature = None
    try:
        previous = json.loads(state_path.read_text())
    except (OSError, ValueError):
        previous = None
    if fingerprint(signature) == fingerprint(previous):
        return
    if signature is None:
        message = "~/.claude is clean and tracking origin/main again"
    else:
        message = (
            f"~/.claude drifted from claude-config ({signature[0]}): {signature[1][:300]}. "
            "coding-agents-sync cannot pull hook and rule changes until it is reconciled "
            "(land the edit in a claude-config worktree, then git restore the path)."
        )
    sent = send(ALERT_TO, message, timeout=60)
    if sent.returncode:
        reason = (sent.stderr or sent.stdout).strip().splitlines()
        raise subprocess.SubprocessError(
            f"orch-watch-alert exited {sent.returncode}: {(reason[-1] if reason else '')[:300]}"
        )
    state_path.parent.mkdir(parents=True, exist_ok=True)
    pending = state_path.with_suffix(".tmp")
    pending.write_text(json.dumps(fingerprint(signature)))
    os.replace(pending, state_path)


def fingerprint(signature):
    if isinstance(signature, list) and len(signature) == 2 and signature[0] == "failed":
        detail = signature[1].lower()
        if detail.startswith("git fetch") and any(
            word in detail for word in ("timed out", "timeout", "failed to connect", "could not resolve")
        ):
            return ["failed", "git fetch network failure"]
    return signature
