"""Exercise context delivery and managed-hook installation through their CLIs.

Missing identity/ranking context or broken installer ownership would fail these
scenarios. Existing model-policy tests do not cover SessionStart or this hook;
all inputs use files and subprocess stdin, without mocks or production seams.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "config/coding-agents/hooks/factory-context.py"


def test_compact_reinjects_lane_context(tmp_path: Path) -> None:
    lanes = tmp_path / "lanes"
    lane = lanes / "ctxlane"
    lane.mkdir(parents=True)
    (lane / "lane.json").write_text(json.dumps({
        "lane": "ctxlane", "project": "factory", "herdr": {"pane_id": "w9:p42"}, "worktrees": [],
    }))
    lines = [f"line {index}" for index in range(1, 81)]
    lines[0] = "# ctxlane — STATE"
    lines[10] = "- 12:00Z decided: SENTINEL-S04"
    lines[74] = "LINE-75-HIDDEN"
    (lane / "STATE.md").write_text("\n".join(lines))
    agents = tmp_path / "agents"
    agents.mkdir()
    learned = tmp_path / "learned.jsonl"
    rows = [
        {"by": "other", "at": f"2026-10-12T00:00:{index:02d}Z", "problem": f"problem {index}",
         "fix": "resolved", "expires": "2099-01-01T00:00:00Z"}
        for index in range(30)
    ]
    rows[0].update(by="w9:p42", problem="PANE-ROW")
    rows[1].update(problem="EXPIRED-ROW", expires="2000-01-01T00:00:00Z")
    learned.write_text("\n".join(json.dumps(row) for row in rows))
    env = dict(os.environ, FACTORY_LANES_DIR=str(lanes), FACTORY_AGENTS_DIR=str(agents),
               FACTORY_LEARNED_FILE=str(learned), HERDR_PANE_ID="w9:p42")
    env.pop("FACTORY_LANE", None)

    def run(python: str, source: str, cwd: Path, environment: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [python, str(HOOK)],
            input=json.dumps({"hook_event_name": "SessionStart", "source": source, "cwd": str(cwd)}),
            env=environment, capture_output=True, text=True, check=True, timeout=10,
        )

    interpreters = [sys.executable]
    if Path("/usr/bin/python3").is_file():
        interpreters.append("/usr/bin/python3")
    for python in interpreters:
        result = run(python, "compact", tmp_path, env)
        assert result.stderr == ""
        output = json.loads(result.stdout)["hookSpecificOutput"]
        assert output["hookEventName"] == "SessionStart"
        context = output["additionalContext"]
        assert any(line.startswith(
            "Factory role: lead of lane ctxlane (project factory, herdr pane w9:p42)"
        ) for line in context.splitlines())
        for expected in ("Lane state:", "cat ", "PANE-ROW"):
            assert expected in context
        for excluded in ("LINE-75-HIDDEN", "EXPIRED-ROW", "fix: resolved"):
            assert excluded not in context
        assert sum(line.startswith("- [") for line in context.splitlines()) <= 3
        assert len(context.encode()) <= 1024
        startup = run(python, "startup", tmp_path, env)
        assert startup.stdout == "" and startup.returncode == 0
        resumed_env = dict(env)
        resumed_env.pop("HERDR_PANE_ID")
        resumed = json.loads(run(python, "resume", lane, resumed_env).stdout)
        assert resumed["hookSpecificOutput"]["additionalContext"].startswith(
            "Factory role: lead of lane ctxlane"
        )
        unknown = run(python, "compact", tmp_path, dict(env, HERDR_PANE_ID="unknown"))
        assert unknown.stdout == "" and unknown.returncode == 0


def test_install_policy_registers_session_start_hook(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".claude/rules").mkdir(parents=True)
    env = dict(os.environ, HOME=str(home), AGENT_LB_URL="http://127.0.0.1:1",
               ROUTE_MODELS_CACHE=str(tmp_path / "models-cache.json"))
    for key in ("ROUTE_FIXTURE_DIR", "ROUTE_TABLE"):
        env.pop(key, None)

    def install(*options: str) -> None:
        subprocess.run(
            [sys.executable, str(ROOT / "config/coding-agents/install-policy.py"),
             "--home", str(home), *options],
            env=env, capture_output=True, text=True, check=True, timeout=30,
        )

    def groups() -> list:
        settings = json.loads((home / ".claude/settings.json").read_text())
        return [group for group in settings.get("hooks", {}).get("SessionStart", [])
                if group.get("matcher") == "compact|resume"
                and any("hooks/factory-context.py" in hook.get("command", "")
                        for hook in group.get("hooks", []))]

    installed_hook = home / ".claude/hooks/factory-context.py"
    install()
    assert len(groups()) == 1
    assert installed_hook.is_file()
    install()
    assert len(groups()) == 1
    install("--uninstall")
    assert groups() == []
    assert not installed_hook.exists()
