from __future__ import annotations

import sys
from pathlib import Path


def finish_text() -> str:
    for path in (
        Path(__file__).resolve().parent.parent / "config/coding-agents/FINISH.md",
        Path.home() / ".agents/policy/coding-agents/FINISH.md",
        Path.home() / ".agent-lb/managed/coding-agents/FINISH.md",
    ):
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            pass
    print("seat: warning: FINISH.md unavailable; omitting finish contract", file=sys.stderr)
    return ""


def final_prompt(prompt: str, task_class: str | None = None) -> str:
    if task_class == "verify" or "END HANDOFF" in prompt.splitlines():
        return prompt
    finish = finish_text()
    return prompt + "\n\n" + finish if finish else prompt
