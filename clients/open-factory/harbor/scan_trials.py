#!/usr/bin/env python3
"""Flag Harbor trials whose agent tried to reach the task's upstream history.

The egress allowlist is the primary guard (see README.md). This is the
after-the-fact check: it reads each trial's ATIF ``agent/trajectory.json`` and
matches the agent's own tool calls (names and arguments, not tool output, since
repo files legitimately mention git commands) against the channels a public
agent-lb or codex-lb fix could arrive through.

    python3 scan_trials.py jobs/<job> [more job or trial dirs...] [--json]

Exits 1 when any trial is flagged or has no trajectory to scan.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PATTERNS: dict[str, re.Pattern[str]] = {
    "upstream-url": re.compile(r"github\.com[/:](aneym/agent-lb|Soju06/codex-lb)", re.I),
    "github-content-host": re.compile(r"(raw\.githubusercontent|codeload\.github|api\.github)\.com", re.I),
    "git-remote-command": re.compile(r"\bgit\b[^\n|;&]*\b(fetch|clone|pull|ls-remote|remote\s+add)\b"),
    "gh-cli": re.compile(r"(^|[\s;&|(\"'])gh\s+(repo|api|pr|search|browse|release)\b"),
    "web-tool": re.compile(r"^(WebSearch|WebFetch|web_search\w*|web_fetch\w*)$"),
}


def tool_calls(trajectory: dict) -> list[tuple[str, str]]:
    calls = []
    for step in trajectory.get("steps", []):
        for call in step.get("tool_calls") or []:
            calls.append((str(call.get("function_name", "")), json.dumps(call.get("arguments", ""))))
    return calls


def scan_trial(trial_dir: Path) -> dict:
    path = trial_dir / "agent" / "trajectory.json"
    if not path.is_file():
        return {"trial": trial_dir.name, "flagged": True, "hits": [{"rule": "no-trajectory"}]}
    hits = []
    for name, arguments in tool_calls(json.loads(path.read_text())):
        for rule, pattern in PATTERNS.items():
            text = name if rule == "web-tool" else arguments
            match = pattern.search(text)
            if match:
                hits.append({"rule": rule, "tool": name, "match": match.group(0)[:120]})
    return {"trial": trial_dir.name, "flagged": bool(hits), "hits": hits}


def trial_dirs(root: Path) -> list[Path]:
    if (root / "agent").is_dir():
        return [root]
    return sorted(path.parent for path in root.glob("*/agent") if path.is_dir())


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    roots = [Path(arg) for arg in argv if arg != "--json"]
    if not roots:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    results = [scan_trial(trial) for root in roots for trial in trial_dirs(root)]
    if as_json:
        print(json.dumps(results, indent=2))
    else:
        for result in results:
            detail = "; ".join(f"{hit['rule']}: {hit.get('match', '')}" for hit in result["hits"])
            print(f"{'CONTAMINATED' if result['flagged'] else 'clean':12} {result['trial']}  {detail}")
    return 1 if any(result["flagged"] for result in results) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
