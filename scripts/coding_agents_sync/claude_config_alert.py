import json
import subprocess
from pathlib import Path

STATE = Path(".agent-lb/claude-config-alert.json")
BLOCKING = ("dirty", "diverged", "off_main", "failed")


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
    if signature == previous:
        return
    if signature is None:
        message = "~/.claude is clean and tracking origin/main again"
    else:
        message = (
            f"~/.claude drifted from claude-config ({signature[0]}): {signature[1][:300]}. "
            "coding-agents-sync cannot pull hook and rule changes until it is reconciled "
            "(land the edit in a claude-config worktree, then git restore the path)."
        )
    sent = subprocess.run(
        ["orch-watch-alert", "--message", message],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if sent.returncode:
        return
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(signature))
