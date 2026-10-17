
from __future__ import annotations

import hashlib
import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "config/coding-agents"
DISPATCH = 'python3 "$HOME/.claude/hooks/hook-dispatch.py"'


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


def processes_naming(text: str) -> list[tuple[int, str]]:

    rows = subprocess.run(["ps", "-axwwE", "-o", "pid=,stat=,command="], capture_output=True, text=True).stdout
    found = []
    for line in rows.splitlines():
        pid, state, rest = (line.split(None, 2) + ["", ""])[:3]
        if text in rest and pid.isdigit() and int(pid) != os.getpid():
            found.append((int(pid), state))
    return found


@pytest.fixture(autouse=True)
def kill_spawned_hooks(tmp_path: Path):

    yield
    for pid, _state in processes_naming(str(tmp_path)):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


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
    install()
    per_hook = hooks_of(home)
    assert not (home / ".claude/hooks/dispatch/registry.json").exists()
    assert "hook-dispatch.py" not in json.dumps(per_hook)

    out = install("--hook-dispatcher", "on").stdout
    assert "hook dispatcher parity passed" in out
    folded = hooks_of(home)
    bash = [group for group in folded["PreToolUse"] if group.get("matcher") == "Bash"]
    registry = json.loads((home / ".claude/hooks/dispatch/registry.json").read_text())


    assert bash == [{"matcher": "Bash", "hooks": [{"type": "command",
                                                   "command": f"{DISPATCH} PreToolUse 'Bash' {registry['rev']}",
                                                   "timeout": 1235}]}]
    bash_entry = [hook["command"] for hook in registry["entries"]["PreToolUse"]["Bash"]]
    assert 'bash "$HOME/.claude/hooks/railway-vars-guard.sh"' in bash_entry
    assert '"$HOME/.claude/hooks/dangerous-command-guard.sh"' in bash_entry

    assert json.loads((home / f".claude/hooks/dispatch/registry.{registry['rev']}.json").read_text()) == registry

    for kept in ({"matcher": "mcp__.*", "hooks": [{"type": "command", "command": "true"}]},
                 {"matcher": "AskUserQuestion", "hooks": [{"type": "command", "command": "true", "timeout": 5}]}):
        assert kept in folded["PreToolUse"]
    assert folded["SessionStart"] == per_hook["SessionStart"]
    assert all("hook-dispatch.py" in group["hooks"][0]["command"] for event in ("UserPromptSubmit", "Stop")
               for group in folded[event]) or folded["Stop"] == per_hook["Stop"]
    report = json.loads((home / ".claude/hooks/dispatch/parity-last.json").read_text())

    assert report["parity"] == "pass" and report["floor"] == "pass" and report["floor_replay"]


    payload = {"tool_name": "Bash", "tool_input": {"command": "gh pr merge 5"}, "cwd": str(tmp_path),
               "hook_event_name": "PreToolUse"}
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


def test_missing_registry_fails_closed(tmp_path: Path) -> None:
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


    for event in ("Stop", "UserPromptSubmit", "PostToolUse"):
        other = run_hook(f"{DISPATCH} {event} '*'", {"hook_event_name": event}, env)
        assert other.returncode == 1 and not other.stdout and "--hook-dispatcher off" in other.stderr, event

    (home / ".claude/hooks/dispatch").mkdir()
    (home / ".claude/hooks/dispatch/registry.json").write_text(json.dumps({"entries": {"PreToolUse": {}}}))
    assert run_hook(f"{DISPATCH} PreToolUse 'Bash'", payload, env).returncode == 2


def test_settings_edited_during_the_parity_run_are_not_overwritten(tmp_path: Path) -> None:
    home, _ = make_home(tmp_path)
    install, _env = installer(tmp_path, home)
    install()
    settings_path = home / ".claude/settings.json"

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






MERGE_CALL = {"tool_name": "Bash", "tool_input": {"command": "gh pr merge 5"}, "hook_event_name": "PreToolUse"}
MERGE_HOOK = {"type": "command", "command": 'python3 "$HOME/g/merge.py"'}
LINK_GUARD = ROOT / "tests/fixtures/hook-dispatch/link-cli-guard.sh"
LINK_HOOK = '"$HOME/.claude/hooks/link-cli-guard.sh"'
DISPATCH_BASH = f"{DISPATCH} PreToolUse 'Bash'"


def dispatcher_home(tmp_path: Path, entry: Any) -> tuple[Path, dict]:

    home = tmp_path / "home"
    (home / ".claude/hooks/dispatch").mkdir(parents=True)
    shutil.copy(SOURCE / "hooks/hook-dispatch.py", home / ".claude/hooks/hook-dispatch.py")
    shutil.copy(LINK_GUARD, home / ".claude/hooks/link-cli-guard.sh")
    (home / "g").mkdir()
    (home / "g/merge.py").write_text(MERGE_GUARD)
    registry = {"schema": 1, "entries": {"PreToolUse": {"Bash": entry}}, "inproc": []}
    (home / ".claude/hooks/dispatch/registry.json").write_text(json.dumps(registry))
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("HOOK_DISPATCH_", "BASH_FUNC_")) and key not in ("BASH_ENV", "ENV", "BASHOPTS")}
    return home, env | {"HOME": str(home)}


def decision(result: subprocess.CompletedProcess) -> str:

    if result.returncode == 2:
        return "deny"
    try:
        obj = json.loads(result.stdout)
    except ValueError:
        return "allow"
    specific = obj.get("hookSpecificOutput") or {}
    return "deny" if specific.get("permissionDecision") == "deny" or obj.get("decision") == "block" else "allow"


def per_hook(hooks: list[dict], payload: dict, env: dict) -> str:
    return "deny" if any(decision(run_hook(hook["command"], payload, env)) == "deny" for hook in hooks) else "allow"


@pytest.mark.parametrize("entry", [None, [], [None], "broken"])
def test_a_damaged_entry_list_refuses(tmp_path: Path, entry: Any) -> None:
    _home, env = dispatcher_home(tmp_path, entry)
    result = run_hook(DISPATCH_BASH, MERGE_CALL, env)
    assert result.returncode == 2, result.stdout + result.stderr
    reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "not a list of hooks" in reason or "not a command hook" in reason


@pytest.mark.parametrize("peer", [
    {"hookSpecificOutput": {"permissionDecision": []}},
    {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                            "permissionDecisionReason": 5}},
    {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                            "permissionDecisionReason": "\ud800"}},
], ids=["list-decision", "int-reason", "lone-surrogate"])
def test_malformed_peer_output_still_denies(tmp_path: Path, peer: dict) -> None:
    peer_hook = {"type": "command", "command": "printf %s '" + json.dumps(peer) + "'"}
    _home, env = dispatcher_home(tmp_path, [MERGE_HOOK, peer_hook])
    assert per_hook([MERGE_HOOK, peer_hook], MERGE_CALL, env) == "deny"
    result = run_hook(DISPATCH_BASH, MERGE_CALL, env)
    assert result.returncode == 2, result.stdout + result.stderr
    reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "BLOCKED: merge through the queue" in reason


def test_no_temp_file_refuses(tmp_path: Path) -> None:
    _home, env = dispatcher_home(tmp_path, [MERGE_HOOK])


    result = subprocess.run(["/bin/sh", "-c", DISPATCH_BASH], input=json.dumps(MERGE_CALL), env=env,
                            capture_output=True, text=True, timeout=60,
                            preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0)))
    assert result.returncode == 2, result.stdout + result.stderr
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("variable", ["BASH_ENV", "BASHOPTS"])
def test_env_that_changes_a_pinned_guard_runs_it(tmp_path: Path, variable: str) -> None:
    _home, env = dispatcher_home(tmp_path, [{"type": "command", "command": LINK_HOOK}])
    if shutil.which("jq", path=env.get("PATH")) is None:
        pytest.skip("the link-cli guard reads its input with jq")
    startup = tmp_path / "startup.sh"
    startup.write_text("shopt -s xpg_echo\n")
    env[variable] = str(startup) if variable == "BASH_ENV" else "xpg_echo"

    call = {"tool_name": "Bash", "tool_input": {"command": "eval $'l\\x69nk-cli auth status'"},
            "hook_event_name": "PreToolUse"}
    if run_hook(LINK_HOOK, call, env).returncode != 2:
        pytest.skip(f"this bash ignores {variable} (BASHOPTS needs bash 4.1+)")
    assert decision(run_hook(DISPATCH_BASH, call, env)) == "deny"

    env.pop(variable)
    trace = tmp_path / "trace.jsonl"
    ls = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse"}
    quiet = run_hook(DISPATCH_BASH, ls, env | {"HOOK_DISPATCH_TRACE": str(trace)})
    assert quiet.returncode == 0 and not quiet.stdout
    assert json.loads(trace.read_text().splitlines()[-1])["hooks"][0]["mode"] == "skipped"


def test_a_pinned_guard_with_a_fallback_answer_runs(tmp_path: Path) -> None:
    hook = {"type": "command",
            "command": LINK_HOOK + " || { printf %s '{\"decision\":\"block\",\"reason\":\"startup failed\"}'; }"}
    _home, env = dispatcher_home(tmp_path, [hook])
    startup = tmp_path / "startup.sh"
    startup.write_text("exit 1\n")
    env["BASH_ENV"] = str(startup)
    call = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse"}
    assert per_hook([hook], call, env) == "deny"
    assert decision(run_hook(DISPATCH_BASH, call, env)) == "deny"


DENY_ALL = """import json, sys
sys.stdin.read()
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                         "permissionDecisionReason": "BLOCKED: G"}}))
"""
REWRITE = """import json, sys
sys.stdin.read()
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                         "updatedInput": {"command": %r}}}))
"""


def bash_groups(hooks: dict) -> list[dict]:

    return [group for group in hooks["PreToolUse"]
            if group.get("matcher") in (None, "", "*") or "Bash" in group["matcher"].split("|")]


def test_a_refold_never_runs_old_settings_against_a_weaker_registry(tmp_path: Path) -> None:
    home, _ = make_home(tmp_path)
    (home / "g").mkdir()
    (home / "g/deny.py").write_text(DENY_ALL)
    guard = {"type": "command", "command": 'python3 "$HOME/g/deny.py"'}
    settings = {"hooks": {"PreToolUse": [{"matcher": "Bash|Write", "hooks": [guard]},
                                         {"matcher": "Bash|Write", "hooks": [{"type": "command", "command": "true"},
                                                                             {"type": "command", "command": ":"}]}]}}
    (home / ".claude/settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    install, env = installer(tmp_path, home)
    install("--hook-dispatcher", "on")

    folded = [hook["command"] for group in bash_groups(hooks_of(home)) if group["matcher"] == "Bash|Write"
              for hook in group["hooks"]]
    assert len(folded) == 1 and "hook-dispatch.py" in folded[0]
    call = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse"}
    assert decision(run_hook(folded[0], call, env)) == "deny"


    edited = json.loads((home / ".claude/settings.json").read_text())
    edited["hooks"]["PreToolUse"].append({"matcher": "Write|Read", "hooks": [guard]})
    (home / ".claude/settings.json").write_text(json.dumps(edited, indent=2) + "\n")
    install()
    assert per_hook([hook for group in bash_groups(hooks_of(home)) for hook in group["hooks"]], call, env) == "deny"


    assert decision(run_hook(folded[0], call, env)) == "deny"


def test_a_fold_keeps_config_order_across_overlapping_matchers(tmp_path: Path) -> None:
    home, _ = make_home(tmp_path)
    (home / "g").mkdir()
    (home / "g/wipe.py").write_text(REWRITE % "rm -rf /")
    (home / "g/safe.py").write_text(REWRITE % "echo safe")
    settings = {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "true"}]},
        {"matcher": "Bash|Write", "hooks": [{"type": "command", "command": 'python3 "$HOME/g/wipe.py"'}]},
        {"matcher": "Bash", "hooks": [{"type": "command", "command": 'python3 "$HOME/g/safe.py"'}]},
    ]}}
    (home / ".claude/settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    install, env = installer(tmp_path, home)
    call = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse"}

    def last_rewrite(hooks: dict) -> str | None:
        last = None
        for group in bash_groups(hooks):
            for hook in group["hooks"]:
                out = run_hook(hook["command"], call, env).stdout
                specific = (json.loads(out) if out.strip() else {}).get("hookSpecificOutput") or {}
                if "updatedInput" in specific:
                    last = specific["updatedInput"]["command"]
        return last

    assert last_rewrite(hooks_of(home)) == "echo safe"
    install("--hook-dispatcher", "on")
    assert last_rewrite(hooks_of(home)) == "echo safe"







def parity_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("hook_dispatch_parity", SOURCE / "hooks/hook-dispatch-parity.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def as_result(command: str, done: subprocess.CompletedProcess) -> dict:
    return {"command": command, "code": done.returncode, "out": done.stdout, "err": done.stderr, "timed_out": False}


def test_a_crash_notice_survives_beside_a_json_answer(tmp_path: Path) -> None:

    crash = {"type": "command", "command": "echo 'guard crashed: synthetic' >&2; exit 1"}
    context = {"type": "command", "command": "printf %s '" + json.dumps(
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "ctx"}}) + "'"}
    _home, env = dispatcher_home(tmp_path, [crash, context])
    call = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse"}
    parity = parity_module()
    old = parity.effect("PreToolUse", [as_result(h["command"], run_hook(h["command"], call, env))
                                       for h in (crash, context)])
    new = parity.effect("PreToolUse", [as_result(DISPATCH_BASH, run_hook(DISPATCH_BASH, call, env))])


    assert old["shown"] == "Failed with non-blocking status code: guard crashed: synthetic" and old["context"] == "ctx"
    assert new == old


def test_a_damaged_entry_falls_back_to_a_good_backup(tmp_path: Path) -> None:

    home, env = dispatcher_home(tmp_path, [None])
    good = {"schema": 1, "entries": {"PreToolUse": {"Bash": [MERGE_HOOK]}}, "inproc": []}
    (home / ".claude/hooks/dispatch/registry.json.bak").write_text(json.dumps(good))
    result = run_hook(DISPATCH_BASH, MERGE_CALL, env)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "BLOCKED: merge through the queue" in json.loads(result.stdout)["hookSpecificOutput"][
        "permissionDecisionReason"]


def test_a_payload_that_cannot_be_written_refuses_and_leaves_no_file(tmp_path: Path) -> None:

    _home, env = dispatcher_home(tmp_path, [MERGE_HOOK])
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    env["TMPDIR"] = str(tmp)
    call = dict(MERGE_CALL, padding="x" * 4096)
    result = subprocess.run(["/bin/sh", "-c", DISPATCH_BASH], input=json.dumps(call), env=env,
                            capture_output=True, text=True, timeout=60,
                            preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_FSIZE, (64, 64)))
    assert result.returncode == 2, result.stdout + result.stderr
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert not [name for name in os.listdir(tmp) if name.startswith("hook-dispatch-")]


FAKE_RTK = """#!/bin/sh
input=$(cat)
case "$input" in
  *late-rewriter*) sleep 1.45 ;;
esac
printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","updatedInput":{"command":"rewritten"}}}'
"""


def rewriter_home(tmp_path: Path, wrapper: str = "", timeout: float = 2.45) -> tuple[Path, dict]:
    home, env = dispatcher_home(tmp_path, [{"type": "command", "command": "rtk hook claude", "timeout": timeout}])
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "rtk").write_text(wrapper or FAKE_RTK)
    (bin_dir / "rtk").chmod(0o755)
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    return home, env | {"PATH": f"{bin_dir}:{env['PATH']}", "TMPDIR": str(tmp)}


TIMER_RTK = """#!/usr/bin/env python3
import json, signal, sys
sys.stdin.read()
left = signal.getitimer(signal.ITIMER_REAL)[0]  # a timer the dispatcher left running into this rewriter
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {"command": "%.2f" % left}}}))
"""


def run_owned(command: str, payload: dict, env: dict, timeout: float) -> subprocess.CompletedProcess | None:

    proc = subprocess.Popen(["/bin/sh", "-c", command], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env, text=True, start_new_session=True)
    try:
        out, err = proc.communicate(json.dumps(payload), timeout=timeout)
        return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)
    except subprocess.TimeoutExpired:
        return None
    finally:
        try:
            os.killpg(proc.pid, 9)
        except OSError:
            pass
        proc.wait(timeout=30)


def test_the_execd_rewriter_is_bounded_by_its_own_timeout_and_a_late_rewrite_never_lands(tmp_path: Path) -> None:

    (tmp_path / "t").mkdir()
    _home, env = rewriter_home(tmp_path / "t", TIMER_RTK)
    call = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse"}
    result = run_owned(DISPATCH_BASH, call, env, 30)
    assert result is not None and result.returncode == 0, result
    left = float(json.loads(result.stdout)["hookSpecificOutput"]["updatedInput"]["command"])
    assert 0 < left <= 2.45, left
    assert not [name for name in os.listdir(env["TMPDIR"]) if name.startswith("hook-dispatch-")]
    (tmp_path / "late").mkdir()
    _home, env = rewriter_home(tmp_path / "late", timeout=1)
    late = {"tool_name": "Bash", "tool_input": {"command": "echo late-rewriter"}, "hook_event_name": "PreToolUse"}
    started = time.monotonic()
    result = run_owned(DISPATCH_BASH, late, env, 6)
    seconds = time.monotonic() - started
    assert result is not None, "the rewriter ran to the entry's timeout: its own timer did not end it"
    assert result.returncode in (-signal.SIGALRM, 128 + signal.SIGALRM), result
    assert result.stdout == "" and "updatedInput" not in result.stdout, result
    assert seconds < 6, seconds
    assert not [name for name in os.listdir(env["TMPDIR"]) if name.startswith("hook-dispatch-")]


QUIET_RTK = "#!/usr/bin/env python3\nimport sys\nsys.stdin.read()\n"


@pytest.mark.skipif(sys.platform != "darwin", reason="the observer uses kqueue and libproc")
def test_the_process_watch_counts_whole_trees(tmp_path: Path) -> None:

    parity = parity_module()

    def observe(*args: Any) -> dict:

        for _attempt in range(3):
            seen = parity.observe(*args)
            if seen["observed"]:
                break
        return seen

    call = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse"}).encode()
    env = os.environ | {"TMPDIR": str(tmp_path)}
    tree = ("python3 -c 'import subprocess, sys; [subprocess.run([sys.executable, \"-c\", \"pass\"]) "
            "for _ in range(2)]'; exit 0")
    seen = observe(tree, b"", env, str(tmp_path), 30)
    assert seen["observed"] and seen["processes"] == 4 and seen["hook_layer"] == 1, seen
    (tmp_path / "plain").mkdir()
    _home, env = rewriter_home(tmp_path / "plain", QUIET_RTK)
    plain = observe(DISPATCH_BASH, call, env, str(tmp_path), 30)
    assert plain["observed"] and plain["processes"] == 1 and plain["hook_layer"] == 1, plain
    (tmp_path / "w").mkdir()
    wrapper = "#!/bin/sh\nsleep 0\nexec /usr/bin/env python3 -c 'import sys; sys.stdin.read()'\n"
    _home, env = rewriter_home(tmp_path / "w", wrapper)
    wrapped = observe(DISPATCH_BASH, call, env, str(tmp_path), 30)
    assert wrapped["observed"] and wrapped["processes"] == 2 and wrapped["hook_layer"] == 1, wrapped
    (tmp_path / "e").mkdir()
    _home, env = dispatcher_home(tmp_path / "e", [{"type": "command", "command": "sleep 0.2"},
                                                  {"type": "command", "command": "sleep 0.3"}])
    spawned = observe(DISPATCH_BASH, call, env, str(tmp_path), 30)
    assert spawned["observed"] and spawned["hook_layer"] == 3 and spawned["processes"] >= 3, spawned


def test_sessions_on_settings_without_a_rev_keep_their_fold(tmp_path: Path) -> None:

    home, _ = make_home(tmp_path)
    install, env = installer(tmp_path, home)
    install("--hook-dispatcher", "on")
    dispatch = home / ".claude/hooks/dispatch"
    registry = json.loads((dispatch / "registry.json").read_text())

    settings = json.loads((home / ".claude/settings.json").read_text())
    text = json.dumps(settings).replace(" " + registry["rev"], "")
    (home / ".claude/settings.json").write_text(json.dumps(json.loads(text), indent=2) + "\n")
    old = {key: value for key, value in registry.items() if key != "rev"}
    (dispatch / "registry.json").write_text(json.dumps(old))
    (dispatch / "registry.json.bak").write_text(json.dumps(old))
    install("--hook-dispatcher", "on")
    assert json.loads((dispatch / "registry.legacy.json").read_text()) == old

    for name in ("registry.json", "registry.json.bak"):
        current = json.loads((dispatch / name).read_text())
        current["entries"]["PreToolUse"].pop("Bash")
        (dispatch / name).write_text(json.dumps(current))
    payload = {"tool_name": "Bash", "tool_input": {"command": "gh pr merge 5"}, "cwd": str(tmp_path),
               "hook_event_name": "PreToolUse"}
    assert decision(run_hook(DISPATCH_BASH, payload, env)) == "deny"





DENY_MERGE = {"type": "command", "command": 'python3 "$HOME/g/merge.py"'}


def write_registry(home: Path, name: str, rev: str | None, entry: list) -> None:
    registry = {"schema": 1, "entries": {"PreToolUse": {"Bash": entry}}, "inproc": []}
    if rev:
        registry["rev"] = rev
    (home / ".claude/hooks/dispatch" / name).write_text(json.dumps(registry))


def rev_of(entry: list) -> str:

    entries = {"PreToolUse": {"Bash": entry}}
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def test_an_entry_never_runs_another_folds_registry(tmp_path: Path) -> None:

    true_entry = [{"type": "command", "command": "true"}]
    r1, r2 = rev_of([DENY_MERGE]), rev_of(true_entry)
    home, env = dispatcher_home(tmp_path, true_entry)
    write_registry(home, "registry.json", r2, true_entry)
    write_registry(home, "registry.json.bak", r2, true_entry)
    assert per_hook([DENY_MERGE], MERGE_CALL, env) == "deny"
    old_session = run_hook(f"{DISPATCH_BASH} {r1}", MERGE_CALL, env)
    assert old_session.returncode == 2, old_session.stdout + old_session.stderr
    assert f"not this entry's {r1}" in json.loads(old_session.stdout)["hookSpecificOutput"][
        "permissionDecisionReason"]
    write_registry(home, "registry.legacy.json", None, [DENY_MERGE])
    no_rev = run_hook(DISPATCH_BASH, MERGE_CALL, env)
    assert decision(no_rev) == "deny" and "BLOCKED: merge through the queue" in no_rev.stdout

    write_registry(home, f"registry.{r1}.json", r1, [DENY_MERGE])
    assert decision(run_hook(f"{DISPATCH_BASH} {r1}", MERGE_CALL, env)) == "deny"
    assert decision(run_hook(f"{DISPATCH_BASH} {r2}", MERGE_CALL, env)) == "allow"


def test_a_registry_edited_after_its_fold_never_answers(tmp_path: Path) -> None:

    folded = [DENY_MERGE, {"type": "command", "command": "true"}]
    rev = rev_of(folded)
    home, env = dispatcher_home(tmp_path, folded)
    for name in (f"registry.{rev}.json", "registry.json", "registry.json.bak"):
        write_registry(home, name, rev, [{"type": "command", "command": "true"}])
    edited = run_hook(f"{DISPATCH_BASH} {rev}", MERGE_CALL, env)
    assert edited.returncode == 2 and "do not hash to rev" in edited.stdout, edited.stdout + edited.stderr
    write_registry(home, "registry.json", rev, folded)
    assert decision(run_hook(f"{DISPATCH_BASH} {rev}", MERGE_CALL, env)) == "deny"




SEAT_FALLBACK = (" 2>/dev/null || { printf %s '{\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\","
                 "\"additionalContext\":\"seat-guard advisory: routing telemetry unavailable\"}}'; }")
LEGACY_SEAT = ('/usr/bin/python3 "$HOME/.claude/hooks/seat-guard.py" 2>/dev/null || '
               '{ printf %s \'{"hookSpecificOutput":'
               '{"hookEventName":"PreToolUse","additionalContext":"seat-guard advisory: routing telemetry unavailable; '
               'dispatch was not blocked. Check agent-lb status."}}\'; }')
DANGEROUS = ("bash -c 'CMD=$(cat | jq -r \".tool_input.command // empty\"); [ -z \"$CMD\" ] && exit 0; "
             "echo \"$CMD\" | grep -qiE \"rm\\s+-rf\\s+/|DROP\\s+(DATABASE|TABLE)\" && { echo \"BLOCKED: "
             "Dangerous command requires explicit user approval.\" >&2; exit 2; }; exit 0'")
FLOOR_LEAVES = {
    "rm-dynamic-deny": ('/usr/bin/python3 "$HOME/.agent-rails/factory-runtime/bin/rm-dynamic-deny"',
                        ".agent-rails/factory-runtime/bin/rm-dynamic-deny", "py"),
    "stash-guard": ('/usr/bin/python3 "$HOME/.agent-rails/factory-runtime/bin/stash-guard"',
                    ".agent-rails/factory-runtime/bin/stash-guard", "py"),
    "link-cli-guard.sh": ('"$HOME/.claude/hooks/link-cli-guard.sh"', ".claude/hooks/link-cli-guard.sh", "sh"),
    "railway-vars-guard.sh": ('"$HOME/.claude/hooks/railway-vars-guard.sh"', ".claude/hooks/railway-vars-guard.sh",
                              "sh"),
    "workflow-relay-guard.py": ('/usr/bin/python3 "$HOME/.claude/hooks/workflow-relay-guard.py"',
                                ".claude/hooks/workflow-relay-guard.py", "py"),
    "seat-guard.py": ('/usr/bin/python3 "$HOME/.claude/hooks/seat-guard.py"' + SEAT_FALLBACK,
                      ".claude/hooks/seat-guard.py", "py"),
    "workflow-seat-guard.py": ('/usr/bin/python3 "$HOME/.claude/hooks/workflow-seat-guard.py"' + SEAT_FALLBACK,
                               ".claude/hooks/workflow-seat-guard.py", "py"),
    "plutil-guard.sh": ('"$HOME/.claude/hooks/plutil-guard.sh"', ".claude/hooks/plutil-guard.sh", "sh"),


    "dangerous-command-guard.sh": ('"$HOME/.claude/hooks/dangerous-command-guard.sh"',
                                   ".claude/hooks/dangerous-command-guard.sh", "sh"),
    "dangerous-command-guard.sh:legacy inline": (DANGEROUS, ".claude/hooks/dangerous-command-guard.sh", "sh"),
    "dangerous-command:inline copy": (DANGEROUS + " ", None, "sh"),

    "seat-guard.py:legacy wrapper": (LEGACY_SEAT, ".claude/hooks/seat-guard.py", "py"),


    "seat-guard.py:bash -c": ("bash -c 'python3 \"$HOME/.claude/hooks/seat-guard.py\" || true'",
                              ".claude/hooks/seat-guard.py", "py"),

    "wide-scan-guard.sh": ('"$HOME/.claude/hooks/wide-scan-guard.sh"', ".claude/hooks/wide-scan-guard.sh", "sh"),


    "seat-guard.py:python3 -u": ('python3 -u "$HOME/.claude/hooks/seat-guard.py" 2>/dev/null || true',
                                 ".claude/hooks/seat-guard.py", "py"),


    "seat-guard.py:|| true;": ('python3 -u "$HOME/.claude/hooks/seat-guard.py" 2>/dev/null || true;',
                               ".claude/hooks/seat-guard.py", "py"),
    "seat-guard.py:||true &": ('/usr/bin/python3 "$HOME/.claude/hooks/seat-guard.py" ||true & ',
                               ".claude/hooks/seat-guard.py", "py"),


    "seat-guard.py:|| true # comment": ('python3 -u "$HOME/.claude/hooks/seat-guard.py" 2>/dev/null || true # advisory',
                                        ".claude/hooks/seat-guard.py", "py"),

    "seat-guard.py:|| exit 0": ('python3 -u "$HOME/.claude/hooks/seat-guard.py" 2>/dev/null || exit 0',
                                ".claude/hooks/seat-guard.py", "py"),

    "agent-lb-bootout-guard.sh": ('"$HOME/.claude/hooks/agent-lb-bootout-guard.sh"',
                                  ".claude/hooks/agent-lb-bootout-guard.sh", "sh"),
}
FAIL_STUBS = {
    "py": {"timeout": "import time\ntime.sleep(30)\n", "crash": "raise RuntimeError('synthetic floor crash')\n",
           "malformed": "import sys\nsys.stdin.read()\nprint('not a hook answer {')\n",
           "no-receipt": "import sys\nsys.stdin.read()\n"},
    "sh": {"timeout": "#!/bin/bash\nexec sleep 30\n", "crash": "#!/bin/bash\necho 'synthetic crash' >&2\nexit 1\n",
           "missing": "#!/bin/bash\necho \"bash: $0: No such file or directory\" >&2\nexit 127\n",
           "malformed": "#!/bin/bash\ncat >/dev/null\necho 'not a hook answer {'\n",
           "no-receipt": "#!/bin/bash\ncat >/dev/null\nexit 0\n"},
}

REACHES_PREFILTERS = "echo railway link-cli launchctl; rm -" "rf /tmp/never-run"
INPROC = ["rm-dynamic-deny", "stash-guard", "workflow-relay-guard.py"]


def failing_guard_home(tmp_path: Path, command: str, rel: str | None, kind: str, mode: str) -> tuple[Path, dict]:
    hook = {"type": "command", "command": command, "timeout": 1 if mode == "timeout" else 20}
    home, env = dispatcher_home(tmp_path, [hook])
    registry = json.loads((home / ".claude/hooks/dispatch/registry.json").read_text())
    (home / ".claude/hooks/dispatch/registry.json").write_text(json.dumps(dict(registry, inproc=INPROC)))
    if rel is None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "bash").write_text(FAIL_STUBS["sh"][mode])
        (bin_dir / "bash").chmod(0o755)
        env = env | {"PATH": f"{bin_dir}:{env['PATH']}"}
    elif mode == "missing":
        (home / rel).unlink(missing_ok=True)
    else:
        path = home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(("#!/usr/bin/env python3\n" if kind == "py" else "") + FAIL_STUBS[kind][mode])
        path.chmod(0o755)
    return home, env


@pytest.mark.parametrize("mode", ["timeout", "crash", "missing", "malformed", "no-receipt"])
@pytest.mark.parametrize("leaf", sorted(FLOOR_LEAVES))
def test_a_floor_guard_that_fails_refuses_the_call(tmp_path: Path, leaf: str, mode: str) -> None:

    command, rel, kind = FLOOR_LEAVES[leaf]
    home, env = failing_guard_home(tmp_path, command, rel, kind, mode)
    call = {"tool_name": "Bash", "tool_input": {"command": REACHES_PREFILTERS}, "hook_event_name": "PreToolUse"}
    result = run_owned(DISPATCH_BASH, call, env, 30)
    if leaf.startswith(("seat-guard.py", "workflow-seat-guard.py", "workflow-relay-guard.py", "wide-scan-guard.sh")):

        if mode == "missing" and kind == "py" and not any(x in command for x in ("||", "bash -c")):
            assert result is not None and result.returncode == 2
        else:
            assert result is not None and result.returncode != 2, result
        return
    assert result is not None and result.returncode == 2, result
    reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    name = leaf.split(":")[0]
    if not (mode == "missing" and kind == "py"):
        assert f"floor guard {name}" in reason and "refused" in reason, reason
        lines = (home / ".claude/hooks/dispatch/failures.jsonl").read_text().splitlines()
        logged = json.loads(lines[-1])
        assert logged["floor"] == name and logged["outcome"] == "refused"


@pytest.mark.parametrize("mode", ["timeout", "crash", "malformed"])
def test_a_non_floor_guard_that_fails_still_fails_open_and_is_logged(tmp_path: Path, mode: str) -> None:

    command = 'python3 "$HOME/.claude/hooks/no-direct-merge.py"'
    home, env = failing_guard_home(tmp_path, command, ".claude/hooks/no-direct-merge.py", "py", mode)
    call = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse"}
    assert per_hook([{"type": "command", "command": command, "timeout": 1}], call, env) == "allow"
    result = run_owned(DISPATCH_BASH, call, env, 30)
    assert result is not None and decision(result) == "allow", result
    if mode != "malformed":
        logged = json.loads((home / ".claude/hooks/dispatch/failures.jsonl").read_text().splitlines()[-1])
        assert logged["floor"] is None and logged["outcome"] == "failed open"


def test_a_floor_guards_block_stands_under_its_fallback_wrapper(tmp_path: Path) -> None:

    command = 'python3 "$HOME/.agent-rails/factory-runtime/bin/rm-dynamic-deny" 2>/dev/null || true'
    rel = ".agent-rails/factory-runtime/bin/rm-dynamic-deny"
    home, env = failing_guard_home(tmp_path, command, rel, "py", "crash")
    (home / rel).write_text("import sys\nsys.stdin.read()\nsys.stderr.write('seat-guard: blocked model\\n')\n"
                            "sys.exit(2)\n")
    call = {"tool_name": "Agent", "tool_input": {"subagent_type": "Explore", "prompt": "x"},
            "hook_event_name": "PreToolUse"}
    result = run_owned(DISPATCH_BASH, call, env, 30)
    assert result is not None and result.returncode == 2, result
    assert "floor guard rm-dynamic-deny" in json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


def test_an_escaped_command_runs_the_pinned_guard(tmp_path: Path) -> None:

    hook = {"type": "command", "command": DANGEROUS}
    _home, env = dispatcher_home(tmp_path, [hook])
    if shutil.which("jq", path=env.get("PATH")) is None:
        pytest.skip("the inline guard reads its input with jq")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "bash").write_text("#!/bin/sh\nexec /bin/bash -O xpg_echo \"$@\"\n")
    (bin_dir / "bash").chmod(0o755)
    env = env | {"PATH": f"{bin_dir}:{env['PATH']}"}
    call = {"tool_name": "Bash", "tool_input": {"command": "rm -" + "rf \\x2f"}, "hook_event_name": "PreToolUse"}
    assert per_hook([hook], call, env) == "deny"
    assert decision(run_hook(DISPATCH_BASH, call, env)) == "deny"


@pytest.mark.parametrize("matcher, tool, event, expected", [
    (None, "Bash", "PreToolUse", True), ("", "Read", "PreToolUse", True), ("*", "Bash", "PreToolUse", True),
    ("Bash", "Bash", "PreToolUse", True), ("Bash", "Read", "PreToolUse", False),
    ("Edit|Write", "Write", "PreToolUse", True), ("Edit, Write", "Write", "PostToolUse", True),
    ("Task", "Agent", "PreToolUse", True),
    ("^Bash$", "Bash", "PreToolUse", True), ("^Bash$", "BashOutput", "PreToolUse", False),
    ("mcp__.*", "mcp__aside__repl", "PreToolUse", True), (".*", "Read", "PreToolUse", True),
    ("Ba.*h", "Bash", "PreToolUse", True), ("^Task$", "Agent", "PreToolUse", True),
    ("(", "Bash", "PreToolUse", False),
    ("Bash", None, "UserPromptSubmit", True), ("Read", "Bash", "Stop", True),
])
def test_the_fixture_matches_tools_as_claude_code_does(matcher: Any, tool: Any, event: str, expected: bool) -> None:

    assert parity_module().matches(matcher, tool, event) is expected


@pytest.mark.parametrize("first, middle, tool", [("Bash", "Write, Bash", "Bash"), ("Agent", "Task", "Agent"),
                                                   ("Agent", "Write|Task", "Agent"), ("Bash", "*", "Bash"),
                                                   ("Bash", "", "Bash")])
def test_a_fold_keeps_order_past_a_name_list_matcher(tmp_path: Path, first: str, middle: str, tool: str) -> None:

    home, _ = make_home(tmp_path)
    (home / "g").mkdir()
    for name, text in (("a.py", "first"), ("b.py", "second"), ("c.py", "third")):
        (home / "g" / name).write_text(REWRITE % text)
    settings = {"hooks": {"PreToolUse": [
        {"matcher": first, "hooks": [{"type": "command", "command": 'python3 "$HOME/g/a.py"'},
                                     {"type": "command", "command": "true"}]},
        {"matcher": middle, "hooks": [{"type": "command", "command": 'python3 "$HOME/g/b.py"'}]},
        {"matcher": first, "hooks": [{"type": "command", "command": 'python3 "$HOME/g/c.py"'}]},
    ]}}
    (home / ".claude/settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    install, env = installer(tmp_path, home)
    call = {"tool_name": tool, "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse"}
    parity = parity_module()

    def last_rewrite() -> str | None:
        last = None
        for group in hooks_of(home)["PreToolUse"]:
            if not parity.matches(group.get("matcher"), tool):
                continue
            for hook in group["hooks"]:
                out = run_hook(hook["command"], call, env).stdout
                specific = (json.loads(out) if out.strip() else {}).get("hookSpecificOutput") or {}
                last = specific.get("updatedInput", {}).get("command", last)
        return last

    assert last_rewrite() == "third"
    install("--hook-dispatcher", "on")
    assert last_rewrite() == "third"


PRETTIER = ("bash -c 'FILE=$(cat | jq -r \".tool_input.file_path // empty\"); if [[ -n \"$FILE\" && \"$FILE\" =~ "
            "\\.(ts|tsx|js|jsx|json|css|md)$ ]] && (cd \"$(dirname \"$FILE\")\" && npx prettier --find-config-path "
            "\"$FILE\" >/dev/null 2>&1); then npx prettier --write \"$FILE\" 2>/dev/null; fi'")


def test_the_fold_drops_the_global_prettier_hook(tmp_path: Path) -> None:

    import hashlib

    assert hashlib.sha256(PRETTIER.encode()).hexdigest().startswith("55c67a61")
    home, settings = make_home(tmp_path)
    settings["hooks"]["PostToolUse"].insert(0, {"matcher": "Edit|Write|MultiEdit",
                                                "hooks": [{"type": "command", "command": PRETTIER, "timeout": 10}]})
    (home / ".claude/settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    install, _env = installer(tmp_path, home)
    out = install("--hook-dispatcher", "on").stdout
    assert "dropped PostToolUse Edit|Write|MultiEdit: global prettier --write" in out
    registry = json.loads((home / ".claude/hooks/dispatch/registry.json").read_text())
    assert "prettier" not in json.dumps(hooks_of(home)) and "prettier" not in json.dumps(registry)
    install("--hook-dispatcher", "off")
    assert "prettier" not in json.dumps(hooks_of(home))


def test_an_install_puts_the_adopted_wide_scan_guard_in_place(tmp_path: Path) -> None:

    home, _settings = make_home(tmp_path)
    install, env = installer(tmp_path, home)
    install()
    guard = home / ".claude/hooks/wide-scan-guard.sh"
    assert guard.read_bytes() == (SOURCE / "hooks/wide-scan-guard.sh").read_bytes()
    assert os.access(guard, os.X_OK)
    call = {"tool_name": "Bash", "hook_event_name": "PreToolUse", "cwd": str(tmp_path)}
    refused = run_hook(f'"{guard}"', call | {"tool_input": {"command": "rg needle /"}}, env)
    assert refused.returncode == 2 and "BLOCKED: wide-scan-guard: recursive rg over /" in refused.stderr, refused
    allowed = run_hook(f'"{guard}"', call | {"tool_input": {"command": "rg -n needle src"}}, env)
    assert allowed.returncode == 0 and not allowed.stdout, allowed

    mirror = home / ".agents/policy/coding-agents"
    assert os.access(mirror / "hooks/wide-scan-guard.sh", os.X_OK)
    rollback = subprocess.run([sys.executable, str(mirror / "install-policy.py"), "--home", str(home),
                               "--hook-dispatcher", "off"], env=env, capture_output=True, text=True, timeout=600)
    assert rollback.returncode == 0, rollback.stdout + rollback.stderr


    install("--uninstall")
    assert guard.read_bytes() == (SOURCE / "hooks/wide-scan-guard.sh").read_bytes() and os.access(guard, os.X_OK)
    assert not (home / ".agent-lb/managed/coding-agents/wide-scan-guard").exists()
    refused = run_hook(f'"{guard}"', call | {"tool_input": {"command": "rg needle /"}}, env)
    assert refused.returncode == 2 and "BLOCKED: wide-scan-guard: recursive rg over /" in refused.stderr, refused


def test_rollback_passes_over_a_registry_that_cannot_restore_the_guards(tmp_path: Path) -> None:

    home, _settings = make_home(tmp_path)
    install, env = installer(tmp_path, home)
    install("--hook-dispatcher", "on")
    dispatch = home / ".claude/hooks/dispatch"
    registry = json.loads((dispatch / "registry.json").read_text())
    damaged = json.loads(json.dumps(registry))
    damaged["per_hook"]["PreToolUse"] = []
    (dispatch / f"registry.{registry['rev']}.json").write_text(json.dumps(damaged))
    out = install("--hook-dispatcher", "off").stdout
    assert "cannot restore the per-hook config" in out
    assert hooks_of(home)["PreToolUse"] == registry["per_hook"]["PreToolUse"] != []
    payload = {"tool_name": "Bash", "tool_input": {"command": "gh pr merge 5"}, "hook_event_name": "PreToolUse"}
    assert per_hook(bash_groups(hooks_of(home))[0]["hooks"], payload, env) == "deny"

    install("--hook-dispatcher", "on")
    registry = json.loads((dispatch / "registry.json").read_text())
    damaged = json.loads(json.dumps(registry))
    damaged["per_hook"]["PreToolUse"] = []
    before = (home / ".claude/settings.json").read_bytes()
    for name in (f"registry.{registry['rev']}.json", "registry.json", "registry.json.bak"):
        (dispatch / name).write_text(json.dumps(damaged))
    assert install("--hook-dispatcher", "off", check=False).returncode != 0
    assert (home / ".claude/settings.json").read_bytes() == before


FLAKY = "import sys, time\nsys.stdin.read()\nprint(time.time_ns())\n"


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
    runs = [subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             start_new_session=True) for _ in range(2)]
    try:
        outputs = [run.communicate(timeout=600)[0] for run in runs]
    finally:
        for run in runs:
            if run.poll() is None:
                os.killpg(run.pid, 9)
                run.wait(timeout=30)
    assert all(run.returncode == 0 for run in runs), outputs
    hooks = hooks_of(home)
    revs = {hook["command"].split()[-1] for event in hooks for group in hooks[event] for hook in group["hooks"]
            if "hook-dispatch.py" in hook["command"]}
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
        run = subprocess.Popen([sys.executable, str(SOURCE / "hooks/hook-dispatch-parity.py"), "--home", str(home),
                                "--only", "allow-ls"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, start_new_session=True)
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






def policy_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("install_policy", SOURCE / "install-policy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_registry_problem_rejects_edited_entries_and_records():

    policy = policy_module()
    hooks = {"PreToolUse": [{"matcher": "Bash", "hooks": [DENY_MERGE, {"type": "command", "command": "true",
                                                                       "timeout": 1}]}]}
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





COUNTED = """import sys
sys.stdin.read()
with open(sys.argv[1], "a") as handle:
    handle.write("ran\\n")
"""


def test_a_command_shared_with_a_per_hook_group_runs_once_per_call(tmp_path: Path) -> None:

    home, _ = make_home(tmp_path)
    (home / "g").mkdir()
    (home / "g/count.py").write_text(COUNTED)
    counter = tmp_path / "runs.txt"
    guard = {"type": "command", "command": f'python3 "$HOME/g/count.py" "{counter}"'}
    settings = {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [guard, {"type": "command", "command": "true"}]},
        {"matcher": "^Bash$", "hooks": [guard]},
    ]}}
    (home / ".claude/settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    install, env = installer(tmp_path, home)
    call = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse"}
    parity = parity_module()

    def runs_per_call() -> int:
        counter.write_text("")
        seen: list[str] = []
        for group in hooks_of(home)["PreToolUse"]:
            if parity.matches(group.get("matcher"), "Bash"):
                seen += [hook["command"] for hook in group["hooks"] if hook["command"] not in seen]
        for command in seen:
            run_hook(command, call, env)
        return len(counter.read_text().splitlines())

    assert runs_per_call() == 1
    install("--hook-dispatcher", "on")
    assert runs_per_call() == 1


def test_the_rewriter_beside_another_hook_is_the_rtk_on_the_callers_path(tmp_path: Path) -> None:

    home, env = rewriter_home(tmp_path)
    registry_path = home / ".claude/hooks/dispatch/registry.json"
    registry = json.loads(registry_path.read_text())
    registry["entries"]["PreToolUse"]["Bash"].append({"type": "command", "command": "true"})
    registry_path.write_text(json.dumps(registry))
    other = tmp_path / "git-dir"
    other.mkdir()
    git = shutil.which("git", path=env["PATH"])
    assert git
    (other / "git").symlink_to(git)
    (other / "rtk").write_text(FAKE_RTK.replace('"rewritten"', '"other rtk"'))
    (other / "rtk").chmod(0o755)
    bin_dir, rest = env["PATH"].split(os.pathsep, 1)
    env = env | {"PATH": os.pathsep.join([bin_dir, str(other), rest])}
    call = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse"}
    per_hook_out = run_hook("rtk hook claude", call, env).stdout
    assert json.loads(per_hook_out)["hookSpecificOutput"]["updatedInput"]["command"] == "rewritten"
    result = run_owned(DISPATCH_BASH, call, env, 30)
    assert result is not None and result.returncode == 0, result
    assert json.loads(result.stdout)["hookSpecificOutput"]["updatedInput"]["command"] == "rewritten", result



WIDE_SCAN = SOURCE / "hooks/wide-scan-guard.sh"
WIDE_SCAN_COMMANDS = (
    "rg -n foo src", "find . -name '*.py'", "ls -la", "git status", "rg x /tmp/project", "find /tmp -name x",
    "rg foo .", "rg foo ..", "find ..", "fd x ../..", "cd .. && rg foo .", "cd / && rg x src",
    "cd src && cd .. && cd .. && rg x .", "rg x /", "rg x ~", "rg x $HOME", "rg x \"$HOME/repos\"",
    "grep -r x .", "grep -rn x ~/.agent-rails/lanes", "grep -r x ~/.agent-rails/agents/home 2>/dev/null",
    "rg x ~/.agent-rails", "fd x /Volumes/Media500", "bash -c 'cd ~ && rg x .'", "env -S'rg x /'",
    "echo \"$(find ~ -name x)\"", "rg x \"/Volumes/StudioExt/repos/a b/../..\"", "rg x '/tmp/a b/../../..'",
    "cat <<'EOF'\nrg x /\nEOF", "xargs grep -r x ..", "printf x | rg foo /Volumes/Media500 | head",


    "rg foo", "cd && rg x .", "pushd / && rg x .", "find -f / -name x", "rg --files /", "watch " * 9 + "ls",
    "echo \"unbalanced", "bash -c 'echo \"x'", "cat <<EOF\nx", "echo x #\" \necho \"y", "ls -la # it's fine",
    "python3 -c 'print(1)'",

    "bash <<< 'rg x /'", "cat <<< 'rg x /'", "git commit -F - <<'EOF'\nrg x /\nEOF", "bash <<EOF\nrg x /\nEOF",

    "grep -rl x ~/.agent-rails/agents ~/.agent-rails/evidence", "rtk grep x ~/.agent-rails", "rtk grep x src",
    "rtk proxy grep -r x ~", "rtk run -c 'rg x /'", "rtk grep x ~/.agent-rails/lanes/a", "rtk git status",

    "grep -rln x .", "rtk grep -rln cost_cmd --include=*.py .", "rg x /Volumes/StudioExt/repos",
    "grep -rln x /Volumes/StudioExt/repos/agent-lb-wt", "rtk rg x /Volumes/StudioExt/repos/factory-wt/",
    "rg -n x /Volumes/StudioExt/repos/agent-lb-wt/topic", "grep -rln x ..",
)


def rewrite_of(result: subprocess.CompletedProcess) -> Any:
    try:
        return ((json.loads(result.stdout) or {}).get("hookSpecificOutput") or {}).get("updatedInput")
    except ValueError:
        return None


def test_the_pinned_wide_scan_guard_is_skipped_only_where_it_cannot_act(tmp_path: Path) -> None:

    hook = {"type": "command", "command": '"$HOME/.claude/hooks/wide-scan-guard.sh"'}
    home, env = dispatcher_home(tmp_path, [hook])
    installed = home / ".claude/hooks/wide-scan-guard.sh"
    shutil.copy(WIDE_SCAN, installed)
    installed.chmod(0o755)
    pins = policy_module().script_pins(SOURCE)
    assert pins["wide-scan-guard.sh"] == hashlib.sha256(installed.read_bytes()).hexdigest()
    registry_path = home / ".claude/hooks/dispatch/registry.json"
    registry_path.write_text(json.dumps(dict(json.loads(registry_path.read_text()), script_sha=pins)))
    trace = tmp_path / "trace.jsonl"
    cwds = [str(home / "project"), str(home), str(home / ".agent-rails/lanes/a"), "/Volumes/StudioExt/repos/x",
            "/Volumes/StudioExt/repos", "/Volumes/StudioExt/repos/x-wt"]
    differ, skipped = [], set()
    for cwd in cwds:
        for command in WIDE_SCAN_COMMANDS:
            call = {"tool_name": "Bash", "tool_input": {"command": command}, "hook_event_name": "PreToolUse",
                    "cwd": cwd}
            old = run_hook(hook["command"], call, env)
            new = run_hook(DISPATCH_BASH, call, env | {"HOOK_DISPATCH_TRACE": str(trace)})
            if (decision(old), rewrite_of(old)) != (decision(new), rewrite_of(new)):
                differ.append((cwd, command, old.returncode, new.returncode, new.stdout[:200]))
            row = json.loads(trace.read_text().splitlines()[-1])
            if row["hooks"][0].get("mode") == "skipped":
                skipped.add((cwd, command))
    assert not differ, differ
    for command in ("rg -n foo src", "find . -name '*.py'", "ls -la", "rg x /tmp/project", "ls -la # it's fine",
                    "python3 -c 'print(1)'"):
        assert (str(home / "project"), command) in skipped, command






BOOTOUT = SOURCE / "hooks/agent-lb-bootout-guard.sh"
RAW_RESTART = "launch" "ctl kickstart -k gui/501/com.aneyman.agent-lb"


def guard_home(tmp_path: Path, name: str, source: Path, timeout: float = 20) -> tuple[Path, dict, dict]:

    hook = {"type": "command", "command": f'"$HOME/.claude/hooks/{name}"', "timeout": timeout}
    home, env = dispatcher_home(tmp_path, [hook])
    shutil.copy(source, home / ".claude/hooks" / name)
    (home / ".claude/hooks" / name).chmod(0o755)
    return home, env, hook


def stub_bin(tmp_path: Path, env: dict, name: str, text: str) -> dict:
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / name).write_text(text)
    (bin_dir / name).chmod(0o755)
    return env | {"PATH": f"{bin_dir}:{env['PATH']}"}


@pytest.mark.parametrize("jq", [
    "#!/bin/sh\necho 'jq: error: something broke' >&2\nexit 5\n",
    "#!/bin/sh\necho 'sh: jq: command not found' >&2\nexit 127\n",
], ids=["jq-fails", "jq-missing"])
def test_the_wide_scan_guard_refuses_when_jq_fails(tmp_path: Path, jq: str) -> None:

    _home, env, hook = guard_home(tmp_path, "wide-scan-guard.sh", WIDE_SCAN)
    env = stub_bin(tmp_path, env, "jq", jq)
    call = {"tool_name": "Bash", "tool_input": {"command": "rg needle /"}, "hook_event_name": "PreToolUse",
            "cwd": str(tmp_path)}
    old = run_hook(hook["command"], call, env)
    assert old.returncode == 2 and "could not read the hook input" in old.stderr, old
    new = run_owned(DISPATCH_BASH, call, env, 30)
    assert new is not None and decision(new) == "deny", new
    assert "could not read the hook input" in json.loads(new.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


def test_a_guard_missing_a_tool_it_needs_is_never_skipped(tmp_path: Path) -> None:

    home, env, hook = guard_home(tmp_path, "wide-scan-guard.sh", WIDE_SCAN)
    registry_path = home / ".claude/hooks/dispatch/registry.json"
    registry_path.write_text(json.dumps(dict(json.loads(registry_path.read_text()),
                                             script_sha=policy_module().script_pins(SOURCE))))
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("bash", "sh", "cat", "python3", "printf", "env"):
        found = shutil.which(name, path=env["PATH"])
        if found:
            (tools / name).symlink_to(found)
    env = env | {"PATH": str(tools)}
    call = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse",
            "cwd": str(home / "project")}
    assert decision(run_hook(hook["command"], call, env)) == "deny"
    result = run_owned(DISPATCH_BASH, call, env, 30)
    assert result is not None and decision(result) == "deny", result


def test_the_wide_scan_guard_refuses_when_its_scanner_fails(tmp_path: Path) -> None:

    _home, env, hook = guard_home(tmp_path, "wide-scan-guard.sh", WIDE_SCAN)
    real = shutil.which("python3", path=env["PATH"])
    assert real
    env = stub_bin(tmp_path, env, "python3", f"""#!/bin/sh
if [ "$1" = "-" ]; then echo 'Traceback (most recent call last): synthetic scanner crash' >&2; exit 1; fi
exec "{real}" "$@"
""")
    call = {"tool_name": "Bash", "tool_input": {"command": "rg needle /"}, "hook_event_name": "PreToolUse",
            "cwd": str(tmp_path)}
    old = run_hook(hook["command"], call, env)
    assert old.returncode == 2 and "scanner failed (python3 exit 1)" in old.stderr, old
    new = run_owned(DISPATCH_BASH, call, env, 30)
    assert new is not None and decision(new) == "deny", new
    assert "scanner failed" in json.loads(new.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


def test_a_bootout_guard_that_times_out_refuses_a_raw_restart(tmp_path: Path) -> None:

    home, env, _hook = guard_home(tmp_path, "agent-lb-bootout-guard.sh", BOOTOUT, timeout=1)
    (home / ".claude/hooks/agent-lb-bootout-guard.sh").write_text("#!/bin/bash\nexec sleep 30\n")
    call = {"tool_name": "Bash", "tool_input": {"command": RAW_RESTART}, "hook_event_name": "PreToolUse"}
    result = run_owned(DISPATCH_BASH, call, env, 30)
    assert result is not None and result.returncode == 2, result
    reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "floor guard agent-lb-bootout-guard.sh timed out" in reason, reason


def test_the_adopted_bootout_guard_refuses_input_it_cannot_read(tmp_path: Path) -> None:

    _home, env, hook = guard_home(tmp_path, "agent-lb-bootout-guard.sh", BOOTOUT)
    if not (Path("/opt/homebrew/bin/jq").exists() or shutil.which("jq", path=env["PATH"])):
        pytest.skip("the guard reads its input with jq")
    call = {"tool_name": "Bash", "tool_input": {"command": RAW_RESTART}, "hook_event_name": "PreToolUse"}
    refused = run_hook(hook["command"], call, env)
    assert refused.returncode == 2 and "raw launchctl restart of agent-lb" in refused.stderr, refused
    assert "floor: keeps the orchestrators up" in refused.stderr, refused
    assert decision(run_hook(hook["command"], dict(call, tool_input={"command": "ls"}), env)) == "allow"
    broken = subprocess.run(["/bin/sh", "-c", hook["command"]], input="{not json " + RAW_RESTART, env=env,
                            capture_output=True, text=True, timeout=60)
    assert broken.returncode == 2 and "could not read the hook input" in broken.stderr, broken


def test_the_bootout_guard_refuses_a_raw_restart_when_its_matcher_fails(tmp_path: Path) -> None:

    _home, env, hook = guard_home(tmp_path, "agent-lb-bootout-guard.sh", BOOTOUT)
    jq = "/opt/homebrew/bin/jq" if Path("/opt/homebrew/bin/jq").exists() else shutil.which("jq", path=env["PATH"])
    if not jq:
        pytest.skip("the guard reads its input with jq")
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("bash", "sh", "python3", "env"):
        found = shutil.which(name, path=env["PATH"])
        if found:
            (tools / name).symlink_to(found)
    (tools / "jq").symlink_to(jq)
    env = env | {"PATH": str(tools)}
    call = {"tool_name": "Bash", "tool_input": {"command": RAW_RESTART}, "hook_event_name": "PreToolUse"}
    old = run_hook(hook["command"], call, env)
    assert old.returncode == 2 and "could not match the command" in old.stderr, old
    new = run_owned(DISPATCH_BASH, call, env, 30)
    assert new is not None and decision(new) == "deny", new
    assert "could not match the command" in json.loads(new.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert decision(run_hook(hook["command"], dict(call, tool_input={"command": "ls"}), env)) == "allow"


def test_install_derives_the_guard_pins_from_their_source(tmp_path: Path) -> None:

    home, _ = make_home(tmp_path)
    install, env = installer(tmp_path, home)
    install()
    install("--hook-dispatcher", "on")
    registry = json.loads((home / ".claude/hooks/dispatch/registry.json").read_text())
    for name in ("wide-scan-guard.sh", "agent-lb-bootout-guard.sh", "railway-vars-guard.sh"):
        installed = home / ".claude/hooks" / name
        assert registry["script_sha"][name] == hashlib.sha256(installed.read_bytes()).hexdigest(), name
    if shutil.which("jq", path=env["PATH"]) is None:
        pytest.skip("the wide-scan guard reads its input with jq")
    trace = tmp_path / "trace.jsonl"
    hooks = [{"type": "command", "command": '"$HOME/.claude/hooks/wide-scan-guard.sh"'},
             {"type": "command", "command": '"$HOME/.claude/hooks/agent-lb-bootout-guard.sh"'}]
    pinned = tmp_path / "pinned-registry.json"
    pinned.write_text(json.dumps({"schema": 1, "entries": {"PreToolUse": {"Bash": hooks}}, "inproc": [],
                                  "script_sha": registry["script_sha"]}))
    call = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse",
            "cwd": str(home / "project")}
    result = run_hook(DISPATCH_BASH, call, env | {"HOOK_DISPATCH_TRACE": str(trace),
                                                  "HOOK_DISPATCH_REGISTRY": str(pinned)})
    assert decision(result) == "allow", result
    modes = [row.get("mode") for row in json.loads(trace.read_text().splitlines()[-1])["hooks"]]
    assert modes == ["skipped", "skipped"], modes


RAILWAY = SOURCE / "hooks/railway-vars-guard.sh"
RAILWAY_PAYLOADS = [
    {"command": c} for c in (
        "ls -la", "git status", "railway status", "railway variables --set KEY=v", "npx @railway/cli up",
        "echo railway", "RAILWAY_TOKEN=x railway up", "bash -c 'railway up'", "cat <<EOF\nrailway up\nEOF",
        "echo $(railway up)", "railwayish thing", "grep -r railway docs")
] + [{"command": None}, {"command": 5}, {}, None, "text"]


def test_the_pinned_railway_guard_is_skipped_only_where_it_cannot_act(tmp_path: Path) -> None:

    hook = {"type": "command", "command": '"$HOME/.claude/hooks/railway-vars-guard.sh"'}
    home, env, _hook = guard_home(tmp_path, "railway-vars-guard.sh", RAILWAY)
    registry_path = home / ".claude/hooks/dispatch/registry.json"
    registry_path.write_text(json.dumps(dict(json.loads(registry_path.read_text()),
                                             script_sha=policy_module().script_pins(SOURCE))))
    trace = tmp_path / "trace.jsonl"
    differ, skipped = [], set()
    for tool_input in RAILWAY_PAYLOADS:
        call = {"tool_name": "Bash", "tool_input": tool_input, "hook_event_name": "PreToolUse", "cwd": str(tmp_path)}
        old = run_hook(hook["command"], call, env)
        new = run_hook(DISPATCH_BASH, call, env | {"HOOK_DISPATCH_TRACE": str(trace)})
        if decision(old) != decision(new):
            differ.append((tool_input, old.returncode, new.returncode, new.stdout[:200]))
        if json.loads(trace.read_text().splitlines()[-1])["hooks"][0].get("mode") == "skipped":
            skipped.add(json.dumps(tool_input))
    assert not differ, differ
    assert {json.dumps({"command": "ls -la"}), json.dumps({"command": "git status"})} <= skipped, skipped






DANGER = SOURCE / "hooks/dangerous-command-guard.sh"
DROP_DB = 'psql -c "DR' 'OP DATA' 'BASE app"'


def tools_without(tmp_path: Path, env: dict, missing: str, names=("bash", "sh", "cat", "jq", "grep", "python3",
                                                                    "env", "printf")) -> dict:

    tools = tmp_path / f"no-{missing}"
    tools.mkdir()
    for name in names:
        found = shutil.which(name, path=env["PATH"])
        if name != missing and found:
            (tools / name).symlink_to(found)
    return env | {"PATH": str(tools)}


def test_the_wide_scan_guard_refuses_when_cat_fails(tmp_path: Path) -> None:

    _home, env, hook = guard_home(tmp_path, "wide-scan-guard.sh", WIDE_SCAN)
    env = tools_without(tmp_path, env, "cat")
    call = {"tool_name": "Bash", "tool_input": {"command": "rg needle /"}, "hook_event_name": "PreToolUse",
            "cwd": str(tmp_path)}
    assert decision(run_hook(hook["command"], call, env)) == "deny"
    new = run_owned(DISPATCH_BASH, call, env, 30)
    assert new is not None and decision(new) == "deny", new
    assert "wide-scan-guard" in json.loads(new.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("missing", ["jq", "grep"])
@pytest.mark.parametrize("command", ['"$HOME/.claude/hooks/dangerous-command-guard.sh"', DANGEROUS],
                         ids=["script", "legacy-inline"])
def test_the_dangerous_command_guard_refuses_when_its_reader_or_matcher_fails(tmp_path: Path, missing: str,
                                                                              command: str) -> None:

    hook = {"type": "command", "command": command, "timeout": 20}
    home, env = dispatcher_home(tmp_path, [hook])
    shutil.copy(DANGER, home / ".claude/hooks/dangerous-command-guard.sh")
    (home / ".claude/hooks/dangerous-command-guard.sh").chmod(0o755)
    call = {"tool_name": "Bash", "tool_input": {"command": DROP_DB}, "hook_event_name": "PreToolUse"}
    healthy = run_owned(DISPATCH_BASH, call, env, 30)
    assert healthy is not None and decision(healthy) == "deny", healthy
    assert "BLOCKED: Dangerous command" in healthy.stdout
    assert decision(run_owned(DISPATCH_BASH, dict(call, tool_input={"command": "ls"}), env, 30)) == "allow"
    broken = run_owned(DISPATCH_BASH, call, tools_without(tmp_path, env, missing), 30)
    assert broken is not None and decision(broken) == "deny", broken


def test_a_floor_guard_allows_only_with_its_receipt(tmp_path: Path) -> None:

    hook = {"type": "command", "command": '/usr/bin/python3 "$HOME/.claude/hooks/workflow-relay-guard.py"'}
    home, env = dispatcher_home(tmp_path, [hook])
    guard = home / ".claude/hooks/workflow-relay-guard.py"
    shutil.copy(SOURCE / "hooks/workflow-relay-guard.py", guard)
    call = {"tool_name": "Workflow", "tool_input": {"script": "agent('x')"}, "hook_event_name": "PreToolUse"}
    answer = run_hook(DISPATCH_BASH, call, env)
    assert answer.returncode == 0 and "floor-ok" not in answer.stdout, answer
    assert "RELAYED-REQUEST-GUARD" in json.loads(answer.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "floor-ok" not in run_hook(hook["command"], call, env).stdout
    guard.write_text(guard.read_text().replace("print(RECEIPT)", "pass"))
    refused = run_hook(DISPATCH_BASH, call, env)
    assert refused.returncode == 0, refused
    assert "RELAYED-REQUEST-GUARD" in json.loads(refused.stdout)["hookSpecificOutput"]["additionalContext"]


def test_install_refuses_a_floor_guard_that_is_not_a_direct_exec(tmp_path: Path) -> None:

    home, settings = make_home(tmp_path)
    install, _env = installer(tmp_path, home)
    settings["hooks"]["PreToolUse"].append({"matcher": "Agent", "hooks": [
        {"type": "command", "command": "bash -c 'python3 \"$HOME/.agent-rails/factory-runtime/bin/rm-dynamic-deny\" || true'"}]})
    (home / ".claude/settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    before = (home / ".claude/settings.json").read_bytes()
    refused = install("--hook-dispatcher", "on", check=False)
    assert refused.returncode != 0 and "direct exec" in refused.stderr, refused.stdout + refused.stderr
    assert (home / ".claude/settings.json").read_bytes() == before
    assert not (home / ".claude/hooks/dispatch/registry.json").exists()
    settings["hooks"]["PreToolUse"][-1]["hooks"] = [{"type": "command", "command": LEGACY_SEAT, "timeout": 5}]
    settings["hooks"]["PreToolUse"].append({"matcher": "Bash", "hooks": [{"type": "command", "command": DANGEROUS}]})
    (home / ".claude/settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    install("--hook-dispatcher", "on")
    registry = json.loads((home / ".claude/hooks/dispatch/registry.json").read_text())

    commands = [hook["command"] for hooks in registry["entries"]["PreToolUse"].values() for hook in hooks] + [
        hook["command"] for group in hooks_of(home)["PreToolUse"] for hook in group["hooks"]]
    assert '/usr/bin/python3 "$HOME/.claude/hooks/seat-guard.py"' in commands
    assert '"$HOME/.claude/hooks/dangerous-command-guard.sh"' in commands
    assert LEGACY_SEAT not in commands and DANGEROUS not in commands


def test_install_refuses_entries_of_different_revs_without_writing(tmp_path: Path) -> None:

    home, _settings = make_home(tmp_path)
    install, _env = installer(tmp_path, home)
    install("--hook-dispatcher", "on")
    settings = json.loads((home / ".claude/settings.json").read_text())
    rev = json.loads((home / ".claude/hooks/dispatch/registry.json").read_text())["rev"]
    other = "0" * 12 if rev != "0" * 12 else "1" * 12

    stop = settings["hooks"]["Stop"][0]["hooks"][0]
    assert "hook-dispatch.py" in stop["command"] and rev in stop["command"]
    stop["command"] = stop["command"].replace(rev, other)
    (home / ".claude/settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    before = {path: path.read_bytes() for path in (home / ".claude").rglob("*") if path.is_file()}
    refused = install(check=False)
    assert refused.returncode != 0 and "different dispatcher registry revs" in refused.stderr, refused.stderr
    after = {path: path.read_bytes() for path in (home / ".claude").rglob("*") if path.is_file()}
    assert after == before


def test_install_folds_a_guard_whose_source_checkout_is_not_executable(tmp_path: Path) -> None:

    assert not os.access(RAILWAY, os.X_OK), "the source is executable now; this test no longer reaches the bug"
    home, settings = make_home(tmp_path)
    settings["hooks"]["PreToolUse"].append({"matcher": "Bash", "hooks": [
        {"type": "command", "command": '"$HOME/.claude/hooks/railway-vars-guard.sh"', "timeout": 10}]})
    (home / ".claude/settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    install, _env = installer(tmp_path, home)
    folded = install("--hook-dispatcher", "on", check=False)
    assert folded.returncode == 0, folded.stdout[-2000:] + folded.stderr[-2000:]
    registry = json.loads((home / ".claude/hooks/dispatch/registry.json").read_text())
    assert '"$HOME/.claude/hooks/railway-vars-guard.sh"' in [
        hook["command"] for hook in registry["entries"]["PreToolUse"]["Bash"]]
    assert os.access(home / ".claude/hooks/railway-vars-guard.sh", os.X_OK)


def test_the_bootout_guard_accepts_the_rails_cos_approval_record(tmp_path: Path) -> None:

    home, env, hook = guard_home(tmp_path, "agent-lb-bootout-guard.sh", BOOTOUT)
    if not (Path("/opt/homebrew/bin/jq").exists() or shutil.which("jq", path=env["PATH"])):
        pytest.skip("the guard reads its input with jq")
    refs = home / ".agent-rails/lanes/orchestrator-refs"
    refs.mkdir(parents=True)
    (refs / "alex-restart-2026-10-08.md").write_text('Alex, 09:18 ET: "restart agent-lb"\n')
    (refs / "alex-notime-2026-10-08.md").write_text('Alex: "restart agent-lb"\n')
    env = env | {"HOME": str(home)}

    def run(suffix: str) -> subprocess.CompletedProcess:
        call = {"tool_name": "Bash", "tool_input": {"command": RAW_RESTART + suffix}, "hook_event_name": "PreToolUse"}
        return run_hook(hook["command"], call, env)

    assert run(" # alex-approval: ~/.agent-rails/lanes/orchestrator-refs/alex-restart-2026-10-08.md").returncode == 0
    for suffix in ("", " # alex-approval: ~/.agent-rails/lanes/orchestrator-refs/alex-missing-2026-10-08.md",
                   " # alex-approval: ~/.agent-rails/lanes/orchestrator-refs/alex-notime-2026-10-08.md",
                   " # alex-approval: ~/.agent-rails/lanes/orchestrator-refs/../orchestrator-refs/alex-restart-2026-10-08.md"):
        assert run(suffix).returncode == 2, suffix


def test_the_dangerous_command_guard_keeps_only_the_drop_floor(tmp_path: Path) -> None:

    home = tmp_path / "home"
    refs = home / ".agent-rails/lanes/orchestrator-refs"
    refs.mkdir(parents=True)
    (refs / "alex-drop-2026-10-08.md").write_text('Alex, 09:18 ET: "drop the scratch database"\n')
    env = dict(os.environ, HOME=str(home))
    root_delete = "r" "m -r" "f /"

    def run(command: str) -> subprocess.CompletedProcess:
        call = {"tool_name": "Bash", "tool_input": {"command": command}, "hook_event_name": "PreToolUse"}
        return subprocess.run(["bash", str(DANGER)], input=json.dumps(call), env=env, capture_output=True, text=True,
                              timeout=60)

    assert run(f"cat > notes.md <<'EOF'\nthe guard used to block {root_delete} in prose\nEOF").returncode == 0
    assert run(f"ssh studio '{root_delete}tmp/old'").returncode == 0
    blocked = run(DROP_DB)
    assert blocked.returncode == 2 and "floor: destructive data" in blocked.stderr, blocked
    assert "BLOCKED: Dangerous command" in blocked.stderr
    approved = run(DROP_DB + " # alex-approval: ~/.agent-rails/lanes/orchestrator-refs/alex-drop-2026-10-08.md")
    assert approved.returncode == 0, approved
    assert run(DROP_DB + " # alex-approval: ~/.agent-rails/lanes/orchestrator-refs/alex-none-2026-10-08.md").returncode == 2

@pytest.mark.parametrize("command,rc", [
    ("printf '%s\\n' 'DROP TABLE accounts'", 0),
    ('echo "rm -rf /"', 0),
    ("cat > contract <<'EOF'\nDROP TABLE accounts; rm -rf /\nEOF", 0),
    ("cat > brief <<'B'\nrefuse rm -rf / and DROP TABLE accounts\nB", 0),
    ("psql -c 'DROP TABLE accounts'", 2),
    ("psql -c 'TRUNCATE TABLE accounts'", 2),
    ("psql -f {file}", 2),
    ("psql < {file}", 2),
    ("cat {file} | psql", 2),
])
def test_guard_trim_review_sql_counterexamples(tmp_path, command, rc):
    sql = tmp_path / 'drop.sql'
    sql.write_text('DROP TABLE accounts;')
    result = subprocess.run(['bash', str(DANGER)],
                            input=json.dumps({'tool_input': {'command': command.format(file=sql)}}),
                            text=True, capture_output=True)
    assert result.returncode == rc, result.stderr

@pytest.mark.xfail(reason="Accepted residual: SQL comment obfuscation", strict=True)
def test_guard_trim_sql_comment_residual():
    result = subprocess.run(['bash', str(DANGER)],
                            input=json.dumps({'tool_input': {'command': "psql -c 'DROP/**/TABLE accounts;'"}}),
                            text=True, capture_output=True)
    assert result.returncode == 2


@pytest.mark.parametrize("command,rc", [


    (RAW_RESTART, 2), ("sudo " + RAW_RESTART, 2), ("bash -c '" + RAW_RESTART + "'", 2), ("x=$(" + RAW_RESTART + ")", 2),
    ("echo \"$(" + RAW_RESTART + ")\"", 2), ("bash <<'EOF'\n" + RAW_RESTART + "\nEOF", 2),
    ("true && " + RAW_RESTART + " # done", 2), ("cat <<EOF\n$(" + RAW_RESTART + ")\nEOF", 2),
    ("cat > notes.md <<'EOF'\n" + RAW_RESTART + "\nEOF", 0), ("echo '" + RAW_RESTART + "'", 0),
    ("git commit -m 'guard: " + RAW_RESTART + " is refused'", 0), ("ssh ax42 '" + RAW_RESTART + "'", 0),
    ("echo '$(" + RAW_RESTART + ")'", 0), ("launch" "ctl print gui/501/com.aneyman.agent-lb", 0),
])
def test_the_bootout_guard_reads_only_the_command_that_runs(tmp_path, command, rc):
    result = subprocess.run(['bash', str(BOOTOUT)], input=json.dumps({'tool_input': {'command': command}}),
                            text=True, capture_output=True, env={**os.environ, 'HOME': str(tmp_path)})
    assert result.returncode == rc, result.stderr


@pytest.mark.parametrize("guard,command", [
    (DANGER, "psql -c 'DROP TABLE accounts'"),
    (SOURCE / 'hooks/agent-lb-bootout-guard.sh', 'launchctl bootout gui/501/com.aneyman.agent-lb'),
])
@pytest.mark.parametrize("prefix,suffix,rc", [
    ("", " # alex-approval: {ref}", 0),
    ("# alex-approval: {ref}\n", "", 2),
    ("", " x# alex-approval: {ref}", 2),
    ("", " ' # alex-approval: {ref}'", 2),
])
def test_guard_trim_approval_comment_boundary(tmp_path, guard, command, prefix, suffix, rc):
    refs = tmp_path / '.agent-rails/lanes/orchestrator-refs'
    refs.mkdir(parents=True)
    ref = refs / 'alex-test-2026-10-08.md'
    ref.write_text('Alex 09:18 approved')
    cmd = prefix.format(ref=ref) + command + suffix.format(ref=ref)
    def run():
        return subprocess.run(['bash', str(guard)], input=json.dumps({'tool_input': {'command': cmd}}),
                              text=True, capture_output=True, env={**os.environ, 'HOME': str(tmp_path)})
    assert run().returncode == rc
    if rc == 0:
        original = refs / 'original'
        ref.rename(original)
        ref.symlink_to(original)
        assert run().returncode == 2


def test_a_hook_child_left_running_or_stopped_is_killed_after_the_parity_run(tmp_path: Path) -> None:

    from importlib.machinery import SourceFileLoader
    parity = SourceFileLoader("hook_dispatch_parity", str(SOURCE / "hooks/hook-dispatch-parity.py")).load_module()
    marker = str(tmp_path / "leftover-marker")
    command = (f"sh -c 'kill -STOP $$; sleep 300 # {marker}' </dev/null >/dev/null 2>&1 & "
               f"sleep 300 {marker} </dev/null >/dev/null 2>&1 & exit 0")
    result = parity.run_shell(command, b"{}", dict(os.environ), str(tmp_path), 30)
    assert result["code"] == 0 and not result["timed_out"]
    deadline = time.monotonic() + 5
    while processes_naming(marker) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert processes_naming(marker) == []
