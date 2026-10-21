from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "config/coding-agents/hooks/seat-guard.py"
CASES = [
    ("Explore", "find where X is defined", "gpt-explorer"),
    ("Explore", "find X in ~/.claude/hooks", "gpt-explorer-local"),
    ("opus-seat", "[class:research] compare A and B for lane foo", "codex-sol"),
    ("general-purpose", "Implement the spec for lane foo's plan: edit x.py", "gpt-implementer"),
    ("general-purpose", "Write the architecture spec for the new service", None),
    ("opus-seat", "OPUS-REASON: ui-critical\nimplement the component", None),
    ("planner", "implement the component", None),
    ("general-purpose", "Implement x.py\nFollow the architecture spec and lane plan", "gpt-implementer"),
    ("general-purpose", "\nNEEDS: land=no local=no\n[class:fix]\nFinish the lane plan", "gpt-implementer"),
    ("opus-seat", "[class:plan] implement the change", None),
    ("fork", "implement the change", None),
    ("general-purpose", "An unclear request", None),
]
CASES += [
    ("opus-seat", f"[class:{tag}] Complete the assigned deliverable", "gpt-implementer")
    for tag in ("implement", "mechanical", "fix", "land", "test", "probe", "sweep", "bulletin")
]
CASES += [("opus-seat", "[class:explore] Locate the owner", "gpt-explorer")]
CASES += [
    ("opus-seat", f"{verb} A and B for lane foo", "codex-sol")
    for verb in ("Research", "Investigate", "Survey", "Compare")
]
CASES += [("opus-seat", "NEEDS: land=no local=yes\nCompare ~/.claude/hooks options", "codex-sol-local")]
CASES += [
    ("general-purpose", f"{verb} the component", "gpt-implementer")
    for verb in ("test", "sweep", "probe", "drain", "bulletin", "reproduce")
]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="seat-hands-", dir=Path(__file__).parent) as temporary:
        home = Path(temporary)
        env = dict(
            os.environ,
            HOME=str(home),
            PATH="",
            ROUTE_LEDGER=str(home / "dispatch.jsonl"),
            ROUTING_TABLE=str(home / "routing-table.json"),
            LEAN_STUDIO_BIN=str(home / "lean-studio"),
            SEAT_GUARD_AGENTS_DIR=str(home / "agents"),
        )
        env.pop("SEAT_GUARD_ALLOW_ANTHROPIC", None)
        for seat, prompt, target in CASES + [("general-purpose", "implement x.py", None)]:
            if (seat, prompt, target) == ("general-purpose", "implement x.py", None):
                env["SEAT_GUARD_ALLOW_ANTHROPIC"] = "1"
            original = {"subagent_type": seat, "prompt": prompt, "model": "opus"}
            done = subprocess.run(
                [sys.executable, str(HOOK)],
                input=json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_input": original}),
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )
            assert done.returncode == 0, done.stderr
            output = json.loads(done.stdout).get("hookSpecificOutput", {}) if done.stdout else {}
            updated = output.get("updatedInput")
            if target is None:
                assert updated is None, (seat, prompt, updated)
            else:
                assert updated and updated["subagent_type"] == target, (seat, prompt, target, output)
                assert "model" not in updated, updated
            print(f"PASS {seat}: {prompt.splitlines()[0] or '(blank header)'} -> {target or 'unchanged'}")
    print(f"seat hands check: {len(CASES) + 1} subprocess cases passed")


if __name__ == "__main__":
    main()
