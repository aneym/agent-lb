from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / "config/coding-agents/hooks"
SECRETS = ("APPROVAL-TEXT-SENTINEL", "APPROVAL-HEREDOC-SENTINEL", "yes ship it")
SESSION = "sess-s5-fixture"


def tool_use(use_id: str, name: str, tool_input: dict, cwd: str = "/tmp/x") -> dict:
    return {"type": "assistant", "cwd": cwd, "sessionId": SESSION,
            "message": {"role": "assistant", "content": [{"type": "tool_use", "id": use_id, "name": name,
                                                           "input": tool_input}]}}


def tool_result(use_id: str, text: str, extra: dict | None = None) -> dict:
    row = {"type": "user", "cwd": "/tmp/x", "sessionId": SESSION,
           "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": use_id, "content": text}]}}
    if extra:
        row["toolUseResult"] = extra
    return row


def write_transcript(path: Path) -> None:
    approval = "alex-via-cos-2026-10-16-fixture-ok"
    rows = [
        {"type": "user", "message": {"role": "user", "content": "filler " * 100_000}},
        tool_use("toolu_bg", "Bash", {"command": "sleep 600", "run_in_background": True}),
        tool_result("toolu_bg", "Command running in background with ID: bk7task9q. Output is being written to: x",
                    {"backgroundTaskId": "bk7task9q"}),
        tool_use("toolu_done", "Bash", {"command": "sleep 1", "run_in_background": True}),
        tool_result("toolu_done", "Command running in background with ID: bdone11x.", {"backgroundTaskId": "bdone11x"}),
        {"type": "queue-operation", "operation": "enqueue", "content":
            "<task-notification>\n<task-id>bdone11x</task-id>\n<tool-use-id>toolu_done</tool-use-id>\n"
            "<status>completed</status>\n</task-notification>"},
        tool_use("toolu_ask", "Bash", {"command": "unblock list --json"}),
        tool_result("toolu_ask", json.dumps({"asks": [{"id": "ecf17a8f", "ticket": "ub_q7k2m9", "title": "x"}]})),
        tool_use("toolu_apr", "Bash", {"command": f"cat ~/.agent-rails/approvals/held-asks/{approval}.md"}),
        tool_result("toolu_apr", f"# {approval}\nAPPROVAL-TEXT-SENTINEL Alex 09:12 ET: yes ship it"),
        tool_use("toolu_doc", "Bash", {"command": "cat > /tmp/x/note <<'EOF'\nAPPROVAL-HEREDOC-SENTINEL\nEOF"}),
        tool_result("toolu_doc", ""),
        tool_use("toolu_seat", "Agent", {"name": "s5-seat", "subagent_type": "opus-seat", "description": "fixture",
                                         "prompt": "AUTHORITY: g-fixture-r0-0123456789ab"}),
        tool_use("toolu_py", "Bash", {"command": ".venv/bin/python -m pytest X"}),
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def run(script: Path, payload: dict, env: dict) -> subprocess.CompletedProcess:
    result = subprocess.run([sys.executable, str(script)], input=json.dumps(payload), env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return result


def session_start(payload: dict, env: dict) -> str:
    result = run(HOOKS / "factory-context.py", {**payload, "hook_event_name": "SessionStart", "source": "compact"},
                 env)
    return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]


def test_precompact_checkpoint_survives_compaction(tmp_path: Path) -> None:
    lanes, agents = tmp_path / "lanes", tmp_path / "agents"
    lane = lanes / "ckl"
    lane.mkdir(parents=True)
    agents.mkdir()
    (lane / "lane.json").write_text(json.dumps({"lane": "ckl", "project": "factory",
                                                "herdr": {"pane_id": "w9:p77"}, "worktrees": []}))
    (lane / "STATE.md").write_text("# ckl\nnext: rerun the check\n")
    transcript = tmp_path / "session.jsonl"
    write_transcript(transcript)
    env = dict(os.environ, FACTORY_LANES_DIR=str(lanes), FACTORY_AGENTS_DIR=str(agents),
               FACTORY_LEARNED_FILE=str(tmp_path / "learned.jsonl"), HERDR_PANE_ID="w9:p77",
               FACTORY_COMPACT_CHECKPOINTS_DIR=str(tmp_path / "fallback"))
    env.pop("FACTORY_LANE", None)
    payload = {"session_id": SESSION, "transcript_path": str(transcript), "cwd": str(tmp_path)}

    pre = run(HOOKS / "compact-checkpoint.py", {**payload, "hook_event_name": "PreCompact", "trigger": "auto"}, env)
    assert len(pre.stdout.encode()) <= 1024
    assert "verbatim" in pre.stdout and ".venv/bin/python -m pytest X" in pre.stdout
    checkpoint_file = lane / "compact-checkpoint.json"
    raw = checkpoint_file.read_text()
    checkpoint = json.loads(raw)
    pending = checkpoint["bash"][-1]
    assert pending["command"] == ".venv/bin/python -m pytest X"
    assert pending["interpreter"] == ".venv/bin/python"
    assert pending["interpreter_path"] == "/tmp/x/.venv/bin/python"
    assert pending["cwd"] == "/tmp/x" and pending["status"] == "pending"
    assert [task["task_id"] for task in checkpoint["background_tasks"]] == ["bk7task9q"]
    assert [seat["name"] for seat in checkpoint["agents"]] == ["s5-seat"]
    assert "ub_q7k2m9" in [ask["id"] for ask in checkpoint["ask_ids"]]
    assert "alex-via-cos-2026-10-16-fixture-ok" in [ref["id"] for ref in checkpoint["approval_ids"]]
    assert "g-fixture-r0-0123456789ab" in [grant["id"] for grant in checkpoint["grant_ids"]]
    assert checkpoint["session_id"] == SESSION and checkpoint["state_md"]["path"] == str(lane / "STATE.md")
    assert checkpoint["transcript"]["offset"] == transcript.stat().st_size
    assert checkpoint["transcript"]["read_from"] > 0

    with transcript.open("a") as stream:
        stream.write(json.dumps({"type": "system", "subtype": "compact_boundary", "sessionId": SESSION}) + "\n")
    fresh = session_start(payload, env)
    assert len(fresh.encode()) <= 1024
    assert str(checkpoint_file) in fresh and "never consent" in fresh
    assert "running task bk7task9q" in fresh and "ask ub_q7k2m9" in fresh
    for text in (pre.stdout, raw, fresh):
        for secret in SECRETS:
            assert secret not in text

    assert "stale" in session_start({**payload, "session_id": "other-session"}, env)
    with transcript.open("a") as stream:
        stream.write(json.dumps({"type": "system", "subtype": "compact_boundary", "sessionId": SESSION}) + "\n")
    assert "stale" in session_start(payload, env)
    transcript.write_text(transcript.read_text().rsplit("\n", 2)[0] + "\n")
    checkpoint["ts"] = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    checkpoint_file.write_text(json.dumps(checkpoint))
    stale = session_start(payload, env)
    assert "stale" in stale and "older than 6 h" in stale and str(checkpoint_file) in stale
    assert "bk7task9q" not in stale and "ub_q7k2m9" not in stale and len(stale.encode()) <= 1024

    orphan_env = dict(env, HERDR_PANE_ID="w9:unknown")
    run(HOOKS / "compact-checkpoint.py", {**payload, "hook_event_name": "PreCompact", "trigger": "manual"},
        orphan_env)
    fallback = tmp_path / "fallback" / f"{SESSION}.json"
    assert json.loads(fallback.read_text())["owner"]["kind"] == "none"
    orphan = session_start(payload, orphan_env)
    assert len(orphan.splitlines()) == 1
    assert orphan.startswith("Factory context: no lane or agent home resolved") and str(fallback) in orphan


def test_install_policy_registers_precompact_hook(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".claude/rules").mkdir(parents=True)
    env = dict(os.environ, HOME=str(home), AGENT_LB_URL="http://127.0.0.1:1",
               ROUTE_MODELS_CACHE=str(tmp_path / "models-cache.json"))
    for key in ("ROUTE_FIXTURE_DIR", "ROUTE_TABLE"):
        env.pop(key, None)

    def install(*options: str) -> str:
        return subprocess.run(
            [sys.executable, str(ROOT / "config/coding-agents/install-policy.py"), "--home", str(home), *options],
            env=env, capture_output=True, text=True, check=True, timeout=60,
        ).stdout

    assert ".claude/hooks/compact-checkpoint.py" in install("--print")
    install()
    install()
    settings = json.loads((home / ".claude/settings.json").read_text())
    commands = [hook["command"] for group in settings["hooks"]["PreCompact"] for hook in group["hooks"]]
    assert commands == ['/usr/bin/python3 "$HOME/.claude/hooks/compact-checkpoint.py"']
    installed = home / ".claude/hooks/compact-checkpoint.py"
    lanes = tmp_path / "lanes" / "ckl"
    lanes.mkdir(parents=True)
    (lanes / "lane.json").write_text(json.dumps({"herdr": {"pane_id": "w9:p78"}}))
    transcript = tmp_path / "t.jsonl"
    write_transcript(transcript)
    hook_env = dict(env, FACTORY_LANES_DIR=str(lanes.parent), FACTORY_AGENTS_DIR=str(tmp_path / "agents"),
                    HERDR_PANE_ID="w9:p78")
    hook_env.pop("FACTORY_LANE", None)
    result = subprocess.run(["/usr/bin/python3" if Path("/usr/bin/python3").is_file() else sys.executable,
                             str(installed)], input=json.dumps({"session_id": SESSION, "cwd": str(tmp_path),
                                                                "transcript_path": str(transcript),
                                                                "hook_event_name": "PreCompact"}),
                            env=hook_env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0 and "verbatim" in result.stdout
    assert json.loads((lanes / "compact-checkpoint.json").read_text())["background_tasks"][0]["task_id"] == "bk7task9q"
    install("--uninstall")
    settings = json.loads((home / ".claude/settings.json").read_text())
    assert "PreCompact" not in settings.get("hooks", {}) and not installed.exists()
