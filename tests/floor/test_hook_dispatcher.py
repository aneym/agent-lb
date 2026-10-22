from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.floor.hook_dispatcher_support import (
    DENY_ALL,
    DENY_MERGE,
    DISPATCH,
    DISPATCH_INSTALLED,
    FLAKY,
    MERGE_GUARD,
    SOURCE,
    bash_groups,
    decision,
    hooks_of,
    installer,
    make_home,
    per_hook,
    policy_module,
    run_hook,
)
from tests.floor.hook_dispatcher_support import kill_spawned_hooks as kill_spawned_hooks


def test_fold_parity_sticky_and_verbatim_rollback(tmp_path: Path) -> None:
    home, _ = make_home(tmp_path)
    install, env = installer(tmp_path, home)
    install()
    per_hook = hooks_of(home)
    assert not (home / ".claude/hooks/dispatch/registry.json").exists()
    assert "hook-dispatch.py" not in json.dumps(per_hook)

    out = install("--hook-dispatcher", "on").stdout
    assert "hook dispatcher parity passed" in out
    folded = hooks_of(home)
    bash = [group for group in folded["PreToolUse"] if group.get("matcher") == "Bash"]
    registry = json.loads((home / ".claude/hooks/dispatch/registry.json").read_text())

    assert bash == [
        {
            "matcher": "Bash",
            "hooks": [
                {
                    "type": "command",
                    "command": f"{DISPATCH_INSTALLED} PreToolUse 'Bash' {registry['rev']}",
                    "timeout": 1265,
                }
            ],
        }
    ]
    bash_entry = [hook["command"] for hook in registry["entries"]["PreToolUse"]["Bash"]]
    assert 'bash "$HOME/.claude/hooks/railway-vars-guard.sh"' in bash_entry
    assert '"$HOME/.claude/hooks/dangerous-command-guard.sh"' in bash_entry

    assert json.loads((home / f".claude/hooks/dispatch/registry.{registry['rev']}.json").read_text()) == registry

    for kept in (
        {"matcher": "mcp__.*", "hooks": [{"type": "command", "command": "true"}]},
        {"matcher": "AskUserQuestion", "hooks": [{"type": "command", "command": "true", "timeout": 5}]},
    ):
        assert kept in folded["PreToolUse"]
    assert folded["SessionStart"] == per_hook["SessionStart"]
    assert (
        all(
            "hook-dispatch.py" in group["hooks"][0]["command"]
            for event in ("UserPromptSubmit", "Stop")
            for group in folded[event]
        )
        or folded["Stop"] == per_hook["Stop"]
    )
    report = json.loads((home / ".claude/hooks/dispatch/parity-last.json").read_text())

    assert report["parity"] == "pass" and report["floor"] == "pass" and report["floor_replay"]

    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "gh pr merge 5"},
        "cwd": str(tmp_path),
        "hook_event_name": "PreToolUse",
    }
    new = run_hook(bash[0]["hooks"][0]["command"], payload, env)
    assert new.returncode == 2
    reason = json.loads(new.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert reason == '[python3 "$HOME/.claude/hooks/no-direct-merge.py"]: BLOCKED: merge through the queue\n'

    before = (home / ".claude/settings.json").read_bytes()
    assert "already converged" in install().stdout
    assert (home / ".claude/settings.json").read_bytes() == before

    added = {"matcher": "Bash", "hooks": [{"type": "command", "command": "true # added later"}]}
    settings = json.loads(before)
    settings["hooks"]["PreToolUse"].append(added)
    (home / ".claude/settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    install()
    refolded = hooks_of(home)
    assert added in refolded["PreToolUse"]
    assert bash[0] in refolded["PreToolUse"]

    guard = home / ".claude/hooks/no-direct-merge.py"
    guard.write_text(MERGE_GUARD + "# edited\n")
    assert "hook dispatcher parity passed" in install().stdout
    registry = json.loads((home / ".claude/hooks/dispatch/registry.json").read_text())
    assert registry["inproc_sha"]["no-direct-merge.py"] == __import__("hashlib").sha256(guard.read_bytes()).hexdigest()

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

    (home / ".claude/hooks/no-direct-merge.py").write_text(
        "import sys, time\nsys.stdin.read()\nprint(time.time_ns())\n"
    )
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


def test_settings_edited_during_the_parity_run_are_not_overwritten(tmp_path: Path) -> None:
    home, _ = make_home(tmp_path)
    install, _env = installer(tmp_path, home)
    install()
    settings_path = home / ".claude/settings.json"

    (home / ".claude/hooks/idle-agents.py").write_text(
        "import json, sys\nsys.stdin.read()\n"
        f"p = {str(settings_path)!r}\n"
        "s = json.load(open(p))\ns['concurrentEdit'] = True\njson.dump(s, open(p, 'w'))\n"
    )
    result = install("--hook-dispatcher", "on", check=False)
    assert result.returncode != 0
    assert "changed while this install ran" in result.stderr
    after = json.loads(settings_path.read_text())
    assert after["concurrentEdit"] is True
    assert "hook-dispatch.py" not in json.dumps(after)
    assert not (home / ".claude/hooks/dispatch/registry.json").exists()


def test_a_refold_never_runs_old_settings_against_a_weaker_registry(tmp_path: Path) -> None:
    home, _ = make_home(tmp_path)
    (home / "g").mkdir()
    (home / "g/deny.py").write_text(DENY_ALL)
    guard = {"type": "command", "command": 'python3 "$HOME/g/deny.py"'}
    settings = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash|Write", "hooks": [guard]},
                {
                    "matcher": "Bash|Write",
                    "hooks": [{"type": "command", "command": "true"}, {"type": "command", "command": ":"}],
                },
            ]
        }
    }
    (home / ".claude/settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    install, env = installer(tmp_path, home)
    install("--hook-dispatcher", "on")

    folded = [
        hook["command"]
        for group in bash_groups(hooks_of(home))
        if group["matcher"] == "Bash|Write"
        for hook in group["hooks"]
    ]
    assert len(folded) == 1 and "hook-dispatch.py" in folded[0]
    call = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse"}
    assert decision(run_hook(folded[0], call, env)) == "deny"

    edited = json.loads((home / ".claude/settings.json").read_text())
    edited["hooks"]["PreToolUse"].append({"matcher": "Write|Read", "hooks": [guard]})
    (home / ".claude/settings.json").write_text(json.dumps(edited, indent=2) + "\n")
    install()
    assert per_hook([hook for group in bash_groups(hooks_of(home)) for hook in group["hooks"]], call, env) == "deny"

    assert decision(run_hook(folded[0], call, env)) == "deny"


def test_a_sticky_run_whose_fixture_fails_keeps_the_fold(tmp_path: Path) -> None:

    home, _ = make_home(tmp_path)
    install, _env = installer(tmp_path, home)
    install("--hook-dispatcher", "on")
    dispatch = home / ".claude/hooks/dispatch"
    before = {name: (dispatch / name).read_bytes() for name in os.listdir(dispatch) if name.startswith("registry")}
    settings_before = json.loads((home / ".claude/settings.json").read_text())
    (home / ".claude/hooks/no-direct-merge.py").write_text(FLAKY)
    out = install().stdout
    assert "running it once more" in out and "keeping the installed fold" in out
    assert json.loads((home / ".claude/settings.json").read_text())["hooks"] == settings_before["hooks"]
    assert {name: (dispatch / name).read_bytes() for name in before} == before


def test_concurrent_installs_leave_settings_and_registry_in_step(tmp_path: Path) -> None:

    home, _ = make_home(tmp_path)
    _install, env = installer(tmp_path, home)
    command = [sys.executable, str(SOURCE / "install-policy.py"), "--home", str(home), "--hook-dispatcher", "on"]
    runs = [
        subprocess.Popen(
            command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True
        )
        for _ in range(2)
    ]
    try:
        outputs = [run.communicate(timeout=600)[0] for run in runs]
    finally:
        for run in runs:
            if run.poll() is None:
                os.killpg(run.pid, 9)
                run.wait(timeout=30)
    assert all(run.returncode == 0 for run in runs), outputs
    hooks = hooks_of(home)
    revs = {
        hook["command"].split()[-1]
        for event in hooks
        for group in hooks[event]
        for hook in group["hooks"]
        if "hook-dispatch.py" in hook["command"]
    }
    assert len(revs) == 1
    rev = revs.pop()
    registry = json.loads((home / f".claude/hooks/dispatch/registry.{rev}.json").read_text())
    assert all(hooks[event] == registry["dispatch"][event] for event in registry["dispatch"])
    assert json.loads((home / ".claude/hooks/dispatch/registry.json").read_text()) == registry


def test_the_fixture_waits_for_a_running_install(tmp_path: Path) -> None:

    import fcntl
    import hashlib
    import time

    home, _ = make_home(tmp_path)
    install, env = installer(tmp_path, home)
    install("--hook-dispatcher", "on")
    digest = hashlib.sha256(str(home.resolve()).encode()).hexdigest()[:16]
    with open(f"/tmp/agent-lb-install-policy-{os.getuid()}-{digest}.lock", "a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        run = subprocess.Popen(
            [sys.executable, str(SOURCE / "hooks/hook-dispatch-parity.py"), "--home", str(home), "--only", "allow-ls"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            time.sleep(3)
            assert run.poll() is None
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            out = run.communicate(timeout=300)[0]
            assert run.returncode == 0, out
        finally:
            if run.poll() is None:
                os.killpg(run.pid, 9)
                run.wait(timeout=30)


def test_registry_problem_rejects_edited_entries_and_records():

    policy = policy_module()
    hooks = {
        "PreToolUse": [{"matcher": "Bash", "hooks": [DENY_MERGE, {"type": "command", "command": "true", "timeout": 1}]}]
    }
    _folded, registry = policy.fold_hooks(hooks)
    assert policy.registry_problem(registry) is None
    emptied = json.loads(json.dumps(registry))
    emptied["entries"], emptied["per_hook"]["PreToolUse"] = {}, []
    assert policy.registry_problem(emptied)
    emptied["rev"] = policy.registry_rev({})
    assert policy.registry_problem(emptied)
    retimed = json.loads(json.dumps(registry))
    retimed["per_hook"]["PreToolUse"][0]["hooks"][1]["timeout"] = 0.001
    assert policy.registry_problem(retimed)


def test_unfold_splits_hooks_another_installer_appended_to_a_dispatcher_group():

    policy = policy_module()
    hooks = {"PreToolUse": [{"matcher": "Bash", "hooks": [DENY_MERGE, {"type": "command", "command": "true"}]}]}
    folded, registry = policy.fold_hooks(hooks)
    added = {"type": "command", "command": "/usr/bin/python3 /opt/other/bash_guards.py", "timeout": 10}
    folded["PreToolUse"][0]["hooks"].append(added)
    unfolded = policy.unfold_hooks(folded, registry)
    assert unfolded["PreToolUse"] == hooks["PreToolUse"] + [{"matcher": "Bash", "hooks": [added]}]
