"""CLI integration tests for the hook dispatcher fold (install-policy.py --hook-dispatcher) and its fail-safe paths.

These are safety floors for every Claude Code session: a fold that loses, reorders or weakens a guard, a rollback
that is not verbatim, or a half-installed state that fails open would each go unnoticed in normal use. The tests
run the real installer, dispatcher and parity fixture against a temp HOME with small guards; no mocks.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "config/coding-agents"
DISPATCH = 'python3 "$HOME/.claude/hooks/hook-dispatch.py"'

# Guards named like reviewed in-process guards, so the dispatcher runs them in-process.
MERGE_GUARD = """import json, sys
data = json.load(sys.stdin)
if "gh pr merge" in (data.get("tool_input") or {}).get("command", ""):
    sys.stderr.write("BLOCKED: merge through the queue\\n")
    sys.exit(2)
"""
HOST_GUARD = """import json, sys
data = json.load(sys.stdin)
if "Herdr Shell" in (data.get("tool_input") or {}).get("command", ""):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": "BLOCKED: not on the host"}}))
"""
QUIET = "import sys\nsys.stdin.read()\n"
CONTEXT = "import sys\nsys.stdin.read()\nprint('context from routing-pulse')\n"


def make_home(tmp_path: Path) -> tuple[Path, dict]:
    home = tmp_path / "home"
    hooks = home / ".claude/hooks"
    hooks.mkdir(parents=True)
    for name, text in (("no-direct-merge.py", MERGE_GUARD), ("herdr-shell-host-guard.py", HOST_GUARD),
                       ("idle-agents.py", QUIET), ("routing-pulse.py", CONTEXT), ("herdr-tab-autoname.py", QUIET)):
        (hooks / name).write_text(text)
    bin_dir = home / ".local/bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "lane-bulletin-hook").write_text("#!/usr/bin/env python3\n" + QUIET)
    (bin_dir / "lane-bulletin-hook").chmod(0o755)
    py = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else "python3"
    settings = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command",
                                               "command": 'python3 "$HOME/.claude/hooks/no-direct-merge.py"'}]},
                {"matcher": "AskUserQuestion", "hooks": [{"type": "command", "command": "true", "timeout": 5}]},
                {"matcher": "Bash", "hooks": [{"type": "command",
                                               "command": f'{py} "$HOME/.claude/hooks/herdr-shell-host-guard.py"'}]},
                {"matcher": "mcp__.*", "hooks": [{"type": "command", "command": "true"}]},
            ],
            "PostToolUse": [
                {"hooks": [{"type": "command", "command": '"$HOME/.local/bin/lane-bulletin-hook" 2>/dev/null || true',
                            "timeout": 3}]},
            ],
            "UserPromptSubmit": [
                {"hooks": [
                    {"type": "command", "command": f'{py} "$HOME/.claude/hooks/routing-pulse.py" 2>/dev/null || true',
                     "timeout": 3, "statusMessage": "Routing pulse"},
                    {"type": "command",
                     "command": f'{py} "$HOME/.claude/hooks/herdr-tab-autoname.py" 2>/dev/null || true',
                     "timeout": 10},
                ]},
            ],
            "Stop": [
                {"hooks": [{"type": "command", "command": f'{py} "$HOME/.claude/hooks/idle-agents.py" stop',
                            "timeout": 10}]},
            ],
            "SessionStart": [{"hooks": [{"type": "command", "command": "true"}]}],
        }
    }
    (home / ".claude/settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    return home, settings


def installer(tmp_path: Path, home: Path):
    env = os.environ | {"HOME": str(home), "AGENT_LB_URL": "http://127.0.0.1:1",
                        "ROUTE_MODELS_CACHE": str(tmp_path / "models-cache.json"), "LANG": "en_US.UTF-8"}
    for key in ("ROUTE_FIXTURE_DIR", "ROUTE_TABLE", "HOOK_DISPATCH_TRACE", "HOOK_DISPATCH_REGISTRY"):
        env.pop(key, None)

    def install(*options: str, check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run([sys.executable, str(SOURCE / "install-policy.py"), "--home", str(home), *options],
                                env=env, capture_output=True, text=True, timeout=600)
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        return result

    return install, env


def hooks_of(home: Path) -> dict:
    return json.loads((home / ".claude/settings.json").read_text())["hooks"]


def run_hook(command: str, payload: dict, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["/bin/sh", "-c", command], input=json.dumps(payload), env=env, capture_output=True,
                          text=True, timeout=60)


def test_fold_parity_sticky_and_verbatim_rollback(tmp_path: Path) -> None:
    home, _ = make_home(tmp_path)
    install, env = installer(tmp_path, home)
    install()  # policy only: the per-hook config stays as it is
    per_hook = hooks_of(home)
    assert not (home / ".claude/hooks/dispatch/registry.json").exists()
    assert "hook-dispatch.py" not in json.dumps(per_hook)

    out = install("--hook-dispatcher", "on").stdout
    assert "hook dispatcher parity passed" in out
    folded = hooks_of(home)
    bash = [group for group in folded["PreToolUse"] if group.get("matcher") == "Bash"]
    assert bash == [{"matcher": "Bash", "hooks": [{"type": "command", "command": f"{DISPATCH} PreToolUse 'Bash'",
                                                   "timeout": 1205}]}]
    # Groups the fold must not touch: a regex matcher, a skipped matcher, other events.
    for kept in ({"matcher": "mcp__.*", "hooks": [{"type": "command", "command": "true"}]},
                 {"matcher": "AskUserQuestion", "hooks": [{"type": "command", "command": "true", "timeout": 5}]}):
        assert kept in folded["PreToolUse"]
    assert folded["SessionStart"] == per_hook["SessionStart"]
    assert all("hook-dispatch.py" in group["hooks"][0]["command"] for event in ("UserPromptSubmit", "Stop")
               for group in folded[event]) or folded["Stop"] == per_hook["Stop"]
    report = json.loads((home / ".claude/hooks/dispatch/parity-last.json").read_text())
    assert report["parity"] == "pass" and report["process_count_max"] <= 2

    # The folded entry gives the per-hook deny, message included.
    payload = {"tool_name": "Bash", "tool_input": {"command": "gh pr merge 5"}, "cwd": str(tmp_path),
               "hook_event_name": "PreToolUse"}
    new = run_hook(bash[0]["hooks"][0]["command"], payload, env)
    assert new.returncode == 2
    reason = json.loads(new.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert reason == '[python3 "$HOME/.claude/hooks/no-direct-merge.py"]: BLOCKED: merge through the queue\n'

    # Sticky: a plain rerun keeps the fold and writes nothing.
    before = (home / ".claude/settings.json").read_bytes()
    assert "already converged" in install().stdout
    assert (home / ".claude/settings.json").read_bytes() == before

    # A hand-added group stays per-hook beside the fold until the next `on`.
    added = {"matcher": "Bash", "hooks": [{"type": "command", "command": "true # added later"}]}
    settings = json.loads(before)
    settings["hooks"]["PreToolUse"].append(added)
    (home / ".claude/settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    install()
    refolded = hooks_of(home)
    assert added in refolded["PreToolUse"]
    assert bash[0] in refolded["PreToolUse"]

    # A changed in-process guard reruns the fixture before the fold keeps it.
    guard = home / ".claude/hooks/no-direct-merge.py"
    guard.write_text(MERGE_GUARD + "# edited\n")
    assert "hook dispatcher parity passed" in install().stdout
    registry = json.loads((home / ".claude/hooks/dispatch/registry.json").read_text())
    assert registry["inproc_sha"]["no-direct-merge.py"] == __import__("hashlib").sha256(guard.read_bytes()).hexdigest()

    # Rollback is one command and verbatim.
    install("--hook-dispatcher", "off")
    restored = hooks_of(home)
    expected = json.loads(json.dumps(per_hook))
    expected["PreToolUse"].append(added)
    assert restored == expected
    assert not (home / ".claude/hooks/dispatch/registry.json").exists()
    assert not (home / ".claude/hooks/dispatch/registry.json.bak").exists()


def test_parity_failure_keeps_the_per_hook_config(tmp_path: Path) -> None:
    home, _ = make_home(tmp_path)
    install, _env = installer(tmp_path, home)
    install()
    before = (home / ".claude/settings.json").read_bytes()
    # A guard whose answer differs on every run cannot pass parity.
    (home / ".claude/hooks/no-direct-merge.py").write_text(
        "import sys, time\nsys.stdin.read()\nprint(time.time_ns())\n")
    result = install("--hook-dispatcher", "on", check=False)
    assert result.returncode != 0
    assert "parity fixture failed" in result.stderr
    assert (home / ".claude/settings.json").read_bytes() == before
    assert not (home / ".claude/hooks/dispatch/registry.json").exists()


def test_uninstall_unfolds_and_removes_the_dispatcher(tmp_path: Path) -> None:
    home, _ = make_home(tmp_path)
    install, _env = installer(tmp_path, home)
    install()
    install("--hook-dispatcher", "on")
    install("--uninstall")
    hooks = hooks_of(home)
    assert "hook-dispatch.py" not in json.dumps(hooks)
    assert not (home / ".claude/hooks/hook-dispatch.py").exists()
    assert not (home / ".claude/hooks/dispatch/registry.json").exists()


def test_missing_registry_fails_closed_for_pre_tool_use_only(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".claude/hooks").mkdir(parents=True)
    shutil.copy(SOURCE / "hooks/hook-dispatch.py", home / ".claude/hooks/hook-dispatch.py")
    env = os.environ | {"HOME": str(home)}
    env.pop("HOOK_DISPATCH_REGISTRY", None)
    payload = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse"}
    pre = run_hook(f"{DISPATCH} PreToolUse 'Bash'", payload, env)
    assert pre.returncode == 2
    assert json.loads(pre.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "--hook-dispatcher off" in pre.stderr
    stop = run_hook(f"{DISPATCH} Stop '*'", {"hook_event_name": "Stop"}, env)
    assert stop.returncode == 1 and not stop.stdout
    # A registry without this entry (settings and registry out of step) refuses the same way.
    (home / ".claude/hooks/dispatch").mkdir()
    (home / ".claude/hooks/dispatch/registry.json").write_text(json.dumps({"entries": {"PreToolUse": {}}}))
    assert run_hook(f"{DISPATCH} PreToolUse 'Bash'", payload, env).returncode == 2


def test_settings_edited_during_the_parity_run_are_not_overwritten(tmp_path: Path) -> None:
    home, _ = make_home(tmp_path)
    install, _env = installer(tmp_path, home)
    install()
    settings_path = home / ".claude/settings.json"
    # The parity fixture runs this guard; it edits the real settings the way a concurrent installer would.
    (home / ".claude/hooks/idle-agents.py").write_text(
        "import json, sys\nsys.stdin.read()\n"
        f"p = {str(settings_path)!r}\n"
        "s = json.load(open(p))\ns['concurrentEdit'] = True\njson.dump(s, open(p, 'w'))\n")
    result = install("--hook-dispatcher", "on", check=False)
    assert result.returncode != 0
    assert "changed while this install ran" in result.stderr
    after = json.loads(settings_path.read_text())
    assert after["concurrentEdit"] is True
    assert "hook-dispatch.py" not in json.dumps(after)
    assert not (home / ".claude/hooks/dispatch/registry.json").exists()
