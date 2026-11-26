from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REAL_GUARD = ROOT / ".claude/hooks/skill_guard.py"


def run_guard(
    script: Path,
    payload: dict,
    *,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("SKIP_CONVENTION_GUARD", None)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def make_isolated_guard(tmp_path: Path, rules: dict) -> tuple[Path, Path]:
    hooks_dir = tmp_path / "hooks"
    skills_dir = tmp_path / "skills"
    hooks_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)
    script = hooks_dir / "skill_guard.py"
    shutil.copy2(REAL_GUARD, script)
    events_path = tmp_path / "events.jsonl"
    (hooks_dir / "_analytics.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        f"EVENTS_PATH = Path({str(events_path)!r})\n"
        "def emit_event(session_id, event_type, data=None):\n"
        "    row = {'session_id': session_id, 'event': event_type, 'data': data or {}}\n"
        "    with EVENTS_PATH.open('a') as fh:\n"
        "        fh.write(json.dumps(row) + '\\n')\n"
    )
    (skills_dir / "skill-rules.json").write_text(json.dumps(rules, indent=2) + "\n")
    return script, events_path


def test_real_project_conventions_remind_is_non_blocking() -> None:
    payload = {"session_id": "test-remind-real", "tool_input": {"file_path": "/tmp/demo/app/main.py"}}
    res = run_guard(REAL_GUARD, payload)
    assert res.returncode == 0, res.stderr
    assert res.stderr == ""
    out = json.loads(res.stdout)
    hook_out = out["hookSpecificOutput"]
    assert hook_out["hookEventName"] == "PreToolUse"
    assert "REMINDER:" in hook_out["additionalContext"]
    assert "project-conventions" in hook_out["additionalContext"]
    assert "BLOCKED:" not in hook_out["additionalContext"]


def test_real_project_conventions_env_override_silences_reminder() -> None:
    payload = {"session_id": "test-remind-skip", "tool_input": {"file_path": "/tmp/demo/app/main.py"}}
    res = run_guard(REAL_GUARD, payload, env_overrides={"SKIP_CONVENTION_GUARD": "1"})
    assert res.returncode == 0, res.stderr
    assert res.stdout == ""
    assert res.stderr == ""


def test_remind_enforcement_exits_zero_and_records_reminded(tmp_path: Path) -> None:
    rules = {
        "version": "1.0",
        "skills": {
            "legacy-remind": {
                "type": "domain",
                "enforcement": "remind",
                "fileTriggers": {"pathPatterns": ["*.py"]},
                "skipConditions": {"sessionSkillUsed": True},
                "blockMessage": "BLOCKED: Run /legacy-remind first.",
            }
        },
    }
    script, events_path = make_isolated_guard(tmp_path, rules)
    payload = {"session_id": "sess-remind", "tool_input": {"file_path": "/tmp/work/foo.py"}}
    res = run_guard(script, payload)
    assert res.returncode == 0, res.stderr
    assert res.stderr == ""
    out = json.loads(res.stdout)
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert out["hookSpecificOutput"]["additionalContext"] == "REMINDER: Run /legacy-remind first."
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    assert len(events) == 1
    assert events[0]["data"]["outcome"] == "reminded"
    assert events[0]["data"]["exit_code"] == 0
    assert events[0]["data"]["matched_count"] == 1


def test_block_enforcement_still_exits_two_and_records_blocked(tmp_path: Path) -> None:
    rules = {
        "version": "1.0",
        "skills": {
            "hard-guard": {
                "type": "guardrail",
                "enforcement": "block",
                "fileTriggers": {"pathPatterns": ["*.py"]},
                "blockMessage": "BLOCKED: Run /hard-guard before editing Python files.",
            }
        },
    }
    script, events_path = make_isolated_guard(tmp_path, rules)
    payload = {"session_id": "sess-block", "tool_input": {"file_path": "/tmp/work/foo.py"}}
    res = run_guard(script, payload)
    assert res.returncode == 2
    assert res.stdout == ""
    assert "BLOCKED: Run /hard-guard before editing Python files." in res.stderr
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    assert len(events) == 1
    assert events[0]["data"]["outcome"] == "blocked"
    assert events[0]["data"]["exit_code"] == 2
