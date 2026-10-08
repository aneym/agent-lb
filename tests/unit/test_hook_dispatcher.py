"""CLI integration tests for the hook dispatcher fold (install-policy.py --hook-dispatcher) and its fail-safe paths.

These are safety floors for every Claude Code session: a fold that loses, reorders or weakens a guard, a rollback
that is not verbatim, or a half-installed state that fails open would each go unnoticed in normal use. The tests
run the real installer, dispatcher and parity fixture against a temp HOME with small guards; no mocks.
"""
from __future__ import annotations

import json
import os
import resource
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

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
    registry = json.loads((home / ".claude/hooks/dispatch/registry.json").read_text())
    assert bash == [{"matcher": "Bash", "hooks": [{"type": "command",
                                                   "command": f"{DISPATCH} PreToolUse 'Bash' {registry['rev']}",
                                                   "timeout": 1205}]}]
    # The settings entries name this fold's own registry file.
    assert json.loads((home / f".claude/hooks/dispatch/registry.{registry['rev']}.json").read_text()) == registry
    # Groups the fold must not touch: a regex matcher, a skipped matcher, other events.
    for kept in ({"matcher": "mcp__.*", "hooks": [{"type": "command", "command": "true"}]},
                 {"matcher": "AskUserQuestion", "hooks": [{"type": "command", "command": "true", "timeout": 5}]}):
        assert kept in folded["PreToolUse"]
    assert folded["SessionStart"] == per_hook["SessionStart"]
    assert all("hook-dispatch.py" in group["hooks"][0]["command"] for event in ("UserPromptSubmit", "Stop")
               for group in folded[event]) or folded["Stop"] == per_hook["Stop"]
    report = json.loads((home / ".claude/hooks/dispatch/parity-last.json").read_text())
    # The install gate is parity and the floor replay; the process budget is the harden check's to prove.
    assert report["parity"] == "pass" and report["floor"] == "pass" and report["floor_replay"]

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
    # The other events' guards are notices and side effects: a non-blocking notice, as a failed guard gave
    # per-hook. A refusal there would erase every prompt (UserPromptSubmit) or keep a session from stopping.
    for event in ("Stop", "UserPromptSubmit", "PostToolUse"):
        other = run_hook(f"{DISPATCH} {event} '*'", {"hook_event_name": event}, env)
        assert other.returncode == 1 and not other.stdout and "--hook-dispatcher off" in other.stderr, event
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


# ---------------------------------------------------------------------------------------------------- fail closed
# One parity case per repro in the 2026-10-07 post-merge review of 4e1cea13: the per-hook config denies, so the
# dispatcher must deny too. Each case fails on 4e1cea13.

MERGE_CALL = {"tool_name": "Bash", "tool_input": {"command": "gh pr merge 5"}, "hook_event_name": "PreToolUse"}
MERGE_HOOK = {"type": "command", "command": 'python3 "$HOME/g/merge.py"'}
LINK_GUARD = ROOT / "tests/fixtures/hook-dispatch/link-cli-guard.sh"  # the bytes pinned in SCRIPT_PREFILTERS
LINK_HOOK = '"$HOME/.claude/hooks/link-cli-guard.sh"'
DISPATCH_BASH = f"{DISPATCH} PreToolUse 'Bash'"


def dispatcher_home(tmp_path: Path, entry: Any) -> tuple[Path, dict]:
    """A home with the dispatcher, a registry whose PreToolUse 'Bash' entry is `entry`, and the guards it names."""
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
    """What Claude Code makes of one hook's result: deny on exit 2 or a JSON deny or block, else allow."""
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
    # Python ignores SIGXFSZ, so under a zero file-size limit every temp file write fails (EFBIG) and no temp dir
    # passes tempfile's probe. stdout stays a pipe.
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
    # Under xpg_echo the guard's `echo "$CMD"` turns \x69 into i, so it reads `link-cli auth status` and blocks.
    call = {"tool_name": "Bash", "tool_input": {"command": "eval $'l\\x69nk-cli auth status'"},
            "hook_event_name": "PreToolUse"}
    if run_hook(LINK_HOOK, call, env).returncode != 2:
        pytest.skip(f"this bash ignores {variable} (BASHOPTS needs bash 4.1+)")
    assert decision(run_hook(DISPATCH_BASH, call, env)) == "deny"
    # Without that environment the prefilter still skips the guard for a call it cannot act on.
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
    """The PreToolUse groups Claude Code runs for a Bash call, in config order."""
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
    folded = [hook["command"] for group in bash_groups(hooks_of(home)) for hook in group["hooks"]]
    assert len(folded) == 1 and "hook-dispatch.py" in folded[0]
    call = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse"}
    assert decision(run_hook(folded[0], call, env)) == "deny"

    # G under Write|Read as well: overlapping matchers now share G, so its group goes back to per-hook.
    edited = json.loads((home / ".claude/settings.json").read_text())
    edited["hooks"]["PreToolUse"].append({"matcher": "Write|Read", "hooks": [guard]})
    (home / ".claude/settings.json").write_text(json.dumps(edited, indent=2) + "\n")
    install()
    assert per_hook([hook for group in bash_groups(hooks_of(home)) for hook in group["hooks"]], call, env) == "deny"
    # Settings from before the refold (a session started earlier, or the moment between the two writes) still
    # run G.
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



# ---------------------------------------------------------------------------------------------------- fix round 2
# The 2026-10-07 parity review of 4e1cea13 and its M1-M5 follow-ups. Each runs the real dispatcher as Claude Code
# does (/bin/sh -c) and compares with the per-hook run through the parity fixture's model of Claude Code.

def parity_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("hook_dispatch_parity", SOURCE / "hooks/hook-dispatch-parity.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def as_result(command: str, done: subprocess.CompletedProcess) -> dict:
    return {"command": command, "code": done.returncode, "out": done.stdout, "err": done.stderr, "timed_out": False}


def test_a_crash_notice_survives_beside_a_json_answer(tmp_path: Path) -> None:
    """A guard that crashes beside one that answers with JSON: Claude Code ignores stderr once stdout is JSON, so the
    notice the user saw per-hook must ride in the merged answer (4e1cea13 exited 0 and dropped it)."""
    crash = {"type": "command", "command": "echo 'guard crashed: synthetic' >&2; exit 1"}
    context = {"type": "command", "command": "printf %s '" + json.dumps(
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "ctx"}}) + "'"}
    _home, env = dispatcher_home(tmp_path, [crash, context])
    call = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse"}
    parity = parity_module()
    old = parity.effect("PreToolUse", [as_result(h["command"], run_hook(h["command"], call, env))
                                       for h in (crash, context)])
    new = parity.effect("PreToolUse", [as_result(DISPATCH_BASH, run_hook(DISPATCH_BASH, call, env))])
    # Claude Code words the notice `Failed with non-blocking status code: <stderr>` (2.1.293); the merged answer
    # carries it in those words, not the bare stderr (review M3 of 4e0dfdaa).
    assert old["shown"] == "Failed with non-blocking status code: guard crashed: synthetic" and old["context"] == "ctx"
    assert new == old


def test_a_damaged_entry_falls_back_to_a_good_backup(tmp_path: Path) -> None:
    """M1: `[null]` in registry.json with a valid backup runs the backup's guards (it used to allow everything)."""
    home, env = dispatcher_home(tmp_path, [None])
    good = {"schema": 1, "entries": {"PreToolUse": {"Bash": [MERGE_HOOK]}}, "inproc": []}
    (home / ".claude/hooks/dispatch/registry.json.bak").write_text(json.dumps(good))
    result = run_hook(DISPATCH_BASH, MERGE_CALL, env)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "BLOCKED: merge through the queue" in json.loads(result.stdout)["hookSpecificOutput"][
        "permissionDecisionReason"]


def test_a_payload_that_cannot_be_written_refuses_and_leaves_no_file(tmp_path: Path) -> None:
    """M2: a temp file that fills mid-write (ENOSPC, here a file-size limit) refuses the call and removes itself."""
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
  *hang-rewriter*) exec sleep 30 ;;
  *slow-rewriter*) sleep 2 ;;
esac
printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","updatedInput":{"command":"rewritten"}}}'
"""


def rewriter_home(tmp_path: Path, wrapper: str = "") -> tuple[Path, dict]:
    home, env = dispatcher_home(tmp_path, [{"type": "command", "command": "rtk hook claude", "timeout": 2.45}])
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
    """Run a hook command in its own process group, as Claude Code does; None when it outlives `timeout` (Claude
    Code cancels it). The group is killed in every case, so nothing outlives the test."""
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


def test_the_execd_rewriter_runs_without_a_timer_and_a_hang_is_cancelled_by_the_entry(tmp_path: Path) -> None:
    """Review M2 of 4e0dfdaa: the exec'd rewriter inherited a timer and a hang ended in SIGALRM, which Claude Code
    shows as `Failed with non-blocking status code: No stderr output`; per-hook the rewriter was cancelled with
    nothing shown. Now no timer reaches it, so a hang lasts until Claude Code's own timeout for the entry, which
    cancels it as before; the payload file is still gone."""
    (tmp_path / "t").mkdir()
    _home, env = rewriter_home(tmp_path / "t", TIMER_RTK)
    call = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse"}
    result = run_owned(DISPATCH_BASH, call, env, 30)
    assert result is not None and result.returncode == 0, result
    assert json.loads(result.stdout)["hookSpecificOutput"]["updatedInput"]["command"] == "0.00"
    assert not [name for name in os.listdir(env["TMPDIR"]) if name.startswith("hook-dispatch-")]
    _home, env = rewriter_home(tmp_path)  # the rewriter's own timeout is 2.45 s
    hang = {"tool_name": "Bash", "tool_input": {"command": "echo hang-rewriter"}, "hook_event_name": "PreToolUse"}
    assert run_owned(DISPATCH_BASH, hang, env, 4.5) is None  # still running past 2.45 s: the entry's timeout ends it
    assert not [name for name in os.listdir(env["TMPDIR"]) if name.startswith("hook-dispatch-")]


QUIET_RTK = "#!/usr/bin/env python3\nimport sys\nsys.stdin.read()\n"  # reads its input, forks nothing


@pytest.mark.skipif(sys.platform != "darwin", reason="the observer uses kqueue and libproc")
def test_the_process_watch_counts_whole_trees(tmp_path: Path) -> None:
    """M4 and the 4e0dfdaa perf review: the count is the whole tree, observed (kqueue plus libproc, the tree stopped at
    each fork), not the dispatcher's audit of its own spawns and not 1 + the root's forks. A shell, a Python parent
    and two Python children is 4 (4e0dfdaa said 2); an rtk wrapper that runs `sleep 0` first is 2; a dispatcher that
    starts two guard processes has 3 hook-layer processes."""
    parity = parity_module()
    call = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse"}).encode()
    env = os.environ | {"TMPDIR": str(tmp_path)}
    tree = ("python3 -c 'import subprocess, sys; [subprocess.run([sys.executable, \"-c\", \"pass\"]) "
            "for _ in range(2)]'; exit 0")
    seen = parity.observe(tree, b"", env, str(tmp_path), 30)
    assert seen["observed"] and seen["processes"] == 4 and seen["hook_layer"] == 1, seen
    (tmp_path / "plain").mkdir()
    _home, env = rewriter_home(tmp_path / "plain", QUIET_RTK)
    plain = parity.observe(DISPATCH_BASH, call, env, str(tmp_path), 30)
    assert plain["observed"] and plain["processes"] == 1 and plain["hook_layer"] == 1, plain
    (tmp_path / "w").mkdir()
    wrapper = "#!/bin/sh\nsleep 0\nexec /usr/bin/env python3 -c 'import sys; sys.stdin.read()'\n"
    _home, env = rewriter_home(tmp_path / "w", wrapper)
    wrapped = parity.observe(DISPATCH_BASH, call, env, str(tmp_path), 30)
    assert wrapped["observed"] and wrapped["processes"] == 2 and wrapped["hook_layer"] == 1, wrapped
    (tmp_path / "e").mkdir()
    _home, env = dispatcher_home(tmp_path / "e", [{"type": "command", "command": "sleep 0.2"},
                                                  {"type": "command", "command": "sleep 0.3"}])
    spawned = parity.observe(DISPATCH_BASH, call, env, str(tmp_path), 30)
    assert spawned["observed"] and spawned["hook_layer"] == 3 and spawned["processes"] >= 3, spawned


def test_sessions_on_settings_without_a_rev_keep_their_fold(tmp_path: Path) -> None:
    """M3: settings written before revs read registry.json; the first fold with revs keeps the old one as
    registry.legacy.json, so a key the new fold drops still runs its guards in sessions that started earlier."""
    home, _ = make_home(tmp_path)
    install, env = installer(tmp_path, home)
    install("--hook-dispatcher", "on")
    dispatch = home / ".claude/hooks/dispatch"
    registry = json.loads((dispatch / "registry.json").read_text())
    # Turn this into a pre-rev install: entries without a rev, a registry without one.
    settings = json.loads((home / ".claude/settings.json").read_text())
    text = json.dumps(settings).replace(" " + registry["rev"], "")
    (home / ".claude/settings.json").write_text(json.dumps(json.loads(text), indent=2) + "\n")
    old = {key: value for key, value in registry.items() if key != "rev"}
    (dispatch / "registry.json").write_text(json.dumps(old))
    (dispatch / "registry.json.bak").write_text(json.dumps(old))
    install("--hook-dispatcher", "on")
    assert json.loads((dispatch / "registry.legacy.json").read_text()) == old
    # The new fold loses the Bash key (simulated): the old session's entry still denies through the legacy fold.
    for name in ("registry.json", "registry.json.bak"):
        current = json.loads((dispatch / name).read_text())
        current["entries"]["PreToolUse"].pop("Bash")
        (dispatch / name).write_text(json.dumps(current))
    payload = {"tool_name": "Bash", "tool_input": {"command": "gh pr merge 5"}, "cwd": str(tmp_path),
               "hook_event_name": "PreToolUse"}
    assert decision(run_hook(DISPATCH_BASH, payload, env)) == "deny"


# ---------------------------------------------------------------------------------------------------- fix round 3
# hook-dispatcher-2 (2026-10-07): the reviews of 4e0dfdaa and the build lead's asks. Each test fails on 4e0dfdaa.

DENY_MERGE = {"type": "command", "command": 'python3 "$HOME/g/merge.py"'}


def write_registry(home: Path, name: str, rev: str | None, entry: list) -> None:
    registry = {"schema": 1, "entries": {"PreToolUse": {"Bash": entry}}, "inproc": []}
    if rev:
        registry["rev"] = rev
    (home / ".claude/hooks/dispatch" / name).write_text(json.dumps(registry))


def test_an_entry_never_runs_another_folds_registry(tmp_path: Path) -> None:
    """Review M1 of 4e0dfdaa: a session whose settings name rev 111111111111 (its Bash guard denies `gh pr merge`)
    after that rev's file is gone, with registry.json now at rev 222222222222 holding only `true`. 4e0dfdaa ran
    `true` and allowed; the entry must refuse instead. An entry without a rev reads the legacy fold, not a newer one."""
    home, env = dispatcher_home(tmp_path, [{"type": "command", "command": "true"}])
    write_registry(home, "registry.json", "222222222222", [{"type": "command", "command": "true"}])
    write_registry(home, "registry.json.bak", "222222222222", [{"type": "command", "command": "true"}])
    assert per_hook([DENY_MERGE], MERGE_CALL, env) == "deny"  # what the session's own fold ran
    old_session = run_hook(f"{DISPATCH_BASH} 111111111111", MERGE_CALL, env)
    assert old_session.returncode == 2, old_session.stdout + old_session.stderr
    assert "not this entry's 111111111111" in json.loads(old_session.stdout)["hookSpecificOutput"][
        "permissionDecisionReason"]
    write_registry(home, "registry.legacy.json", None, [DENY_MERGE])
    no_rev = run_hook(DISPATCH_BASH, MERGE_CALL, env)
    assert decision(no_rev) == "deny" and "BLOCKED: merge through the queue" in no_rev.stdout
    # The entry's own fold still answers from its file, or from registry.json when that carries its rev.
    write_registry(home, "registry.111111111111.json", "111111111111", [DENY_MERGE])
    assert decision(run_hook(f"{DISPATCH_BASH} 111111111111", MERGE_CALL, env)) == "deny"
    assert decision(run_hook(f"{DISPATCH_BASH} 222222222222", MERGE_CALL, env)) == "allow"


# Floor guards as the live settings run them (2026-10-07), each replaced by a stub that fails; the stub bytes never
# match a pinned prefilter, so the dispatcher runs each one.
SEAT_FALLBACK = (" 2>/dev/null || { printf %s '{\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\","
                 "\"additionalContext\":\"seat-guard advisory: routing telemetry unavailable\"}}'; }")
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
    "dangerous-command": (DANGEROUS, None, "sh"),
}
FAIL_STUBS = {
    "py": {"timeout": "import time\ntime.sleep(30)\n", "crash": "raise RuntimeError('synthetic floor crash')\n",
           "malformed": "import sys\nsys.stdin.read()\nprint('not a hook answer {')\n"},
    "sh": {"timeout": "#!/bin/bash\nexec sleep 30\n", "crash": "#!/bin/bash\necho 'synthetic crash' >&2\nexit 1\n",
           "missing": "#!/bin/bash\necho \"bash: $0: No such file or directory\" >&2\nexit 127\n",
           "malformed": "#!/bin/bash\ncat >/dev/null\necho 'not a hook answer {'\n"},
}
# Reaches every pinned floor guard's prefilter, so each runs (a guard its prefilter skips never runs, so cannot fail).
REACHES_PREFILTERS = "echo railway link-cli; rm -" "rf /tmp/never-run"
INPROC = ["rm-dynamic-deny", "stash-guard", "workflow-relay-guard.py"]  # reviewed for in-process runs (install-policy)


def failing_guard_home(tmp_path: Path, command: str, rel: str | None, kind: str, mode: str) -> tuple[Path, dict]:
    hook = {"type": "command", "command": command, "timeout": 1 if mode == "timeout" else 20}
    home, env = dispatcher_home(tmp_path, [hook])
    registry = json.loads((home / ".claude/hooks/dispatch/registry.json").read_text())
    (home / ".claude/hooks/dispatch/registry.json").write_text(json.dumps(dict(registry, inproc=INPROC)))
    if rel is None:  # an inline guard: the `bash` it starts fails
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


@pytest.mark.parametrize("mode", ["timeout", "crash", "missing", "malformed"])
@pytest.mark.parametrize("leaf", sorted(FLOOR_LEAVES))
def test_a_floor_guard_that_fails_refuses_the_call(tmp_path: Path, leaf: str, mode: str) -> None:
    """The simplify lead's ask 1 (2026-10-07): a floor guard that times out, crashes, is missing or prints something
    that is not a hook answer denies, through any wrapper (the seat guards' `|| { printf ...; }` fallback included).
    4e0dfdaa let a crash, a timeout or plain output through as an allow."""
    command, rel, kind = FLOOR_LEAVES[leaf]
    home, env = failing_guard_home(tmp_path, command, rel, kind, mode)
    call = {"tool_name": "Bash", "tool_input": {"command": REACHES_PREFILTERS}, "hook_event_name": "PreToolUse"}
    result = run_owned(DISPATCH_BASH, call, env, 30)
    assert result is not None and result.returncode == 2, result
    reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    if not (mode == "missing" and kind == "py"):  # python3 exits 2 on a missing script: a block of its own
        assert f"floor guard {leaf}" in reason and "refused" in reason, reason
        lines = (home / ".claude/hooks/dispatch/failures.jsonl").read_text().splitlines()
        logged = json.loads(lines[-1])
        assert logged["floor"] == leaf and logged["outcome"] == "refused"


@pytest.mark.parametrize("mode", ["timeout", "crash", "malformed"])
def test_a_non_floor_guard_that_fails_still_fails_open_and_is_logged(tmp_path: Path, mode: str) -> None:
    """Any other guard keeps its per-hook behaviour: a timeout, crash or stray output allows, as it did per-hook;
    the failure is logged."""
    command = 'python3 "$HOME/.claude/hooks/no-direct-merge.py"'
    home, env = failing_guard_home(tmp_path, command, ".claude/hooks/no-direct-merge.py", "py", mode)
    call = {"tool_name": "Bash", "tool_input": {"command": "ls"}, "hook_event_name": "PreToolUse"}
    assert per_hook([{"type": "command", "command": command, "timeout": 1}], call, env) == "allow"
    result = run_owned(DISPATCH_BASH, call, env, 30)
    assert result is not None and decision(result) == "allow", result
    if mode != "malformed":  # exit 0 with stray text is no failure for a non-floor guard
        logged = json.loads((home / ".claude/hooks/dispatch/failures.jsonl").read_text().splitlines()[-1])
        assert logged["floor"] is None and logged["outcome"] == "failed open"


def test_a_floor_guards_block_stands_under_its_fallback_wrapper(tmp_path: Path) -> None:
    """p13D :394 (blocking-wrapper fallback): a seat guard that exits 2 under `|| { printf ...; }` was turned into an
    allow with the advisory, per-hook and on 4e0dfdaa; its block now stands."""
    command, rel, _kind = FLOOR_LEAVES["seat-guard.py"]
    home, env = failing_guard_home(tmp_path, command, rel, "py", "crash")
    (home / rel).write_text("import sys\nsys.stdin.read()\nsys.stderr.write('seat-guard: blocked model\\n')\n"
                            "sys.exit(2)\n")
    call = {"tool_name": "Agent", "tool_input": {"subagent_type": "Explore", "prompt": "x"},
            "hook_event_name": "PreToolUse"}
    result = run_owned(DISPATCH_BASH, call, env, 30)
    assert result is not None and result.returncode == 2, result
    assert "seat-guard: blocked model" in json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


def test_an_escaped_command_runs_the_pinned_guard(tmp_path: Path) -> None:
    """p13D :197 (xpg_echo): under a bash that expands echo escapes, a command spelled with `\\x2f` for the slash
    reaches the inline guard's grep with the slash in it. Its prefilter reads the raw text, so 4e0dfdaa skipped the
    guard and allowed; input with a backslash now always runs it."""
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
    ("Task", "Agent", "PreToolUse", True),  # alias
    ("^Bash$", "Bash", "PreToolUse", True), ("^Bash$", "BashOutput", "PreToolUse", False),
    ("mcp__.*", "mcp__aside__repl", "PreToolUse", True), (".*", "Read", "PreToolUse", True),
    ("Ba.*h", "Bash", "PreToolUse", True), ("^Task$", "Agent", "PreToolUse", True),
    ("(", "Bash", "PreToolUse", False),  # an invalid regex matches nothing
    ("Bash", None, "UserPromptSubmit", True), ("Read", "Bash", "Stop", True),  # no tool: the matcher is ignored
])
def test_the_fixture_matches_tools_as_claude_code_does(matcher: Any, tool: Any, event: str, expected: bool) -> None:
    """The 4e0dfdaa perf review: the fixture read every matcher as a literal `A|B` list, so a per-hook group under
    `^Bash$` or `mcp__.*` never ran on the old path. A unit table: Claude Code's matcher (2.1.293) is a small pure
    algorithm with many forms, and each form is one row here."""
    assert parity_module().matches(matcher, tool, event) is expected


@pytest.mark.parametrize("first, middle, tool", [("Bash", "Write, Bash", "Bash"), ("Agent", "Task", "Agent"),
                                                   ("Agent", "Write|Task", "Agent"), ("Bash", "*", "Bash"),
                                                   ("Bash", "", "Bash")])
def test_a_fold_keeps_order_past_a_name_list_matcher(tmp_path: Path, first: str, middle: str, tool: str) -> None:
    """Review MF (wf_29a26082): `Bash A, * B, Bash C` must not fold to `Bash[A,C], * B`, or the winning rewrite moves
    from C to B. Fixed in b8fbd1a7 (this passes on 4e0dfdaa too); kept as the guard, with Claude Code's name-list
    (`Write, Bash`) and alias (`Task` is Agent) forms, which the fold now reads as Claude Code does."""
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
    """The simplify lead's ask 2: the global PostToolUse `npx prettier --write` leaf goes at the fold (repos format in
    their own checks), from the settings and from the per-hook config a rollback restores."""
    import hashlib

    assert hashlib.sha256(PRETTIER.encode()).hexdigest().startswith("55c67a61")  # the live leaf's exact bytes
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


def test_rollback_passes_over_a_registry_that_cannot_restore_the_guards(tmp_path: Path) -> None:
    """Review of 4e0dfdaa (install-policy rollback): the rev file names a valid Bash deny entry but its per_hook
    block lost PreToolUse; 4e0dfdaa restored PreToolUse as [] (every guard gone). Rollback now uses the good copy."""
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
    # With no good copy at all nothing is written: the folded settings stay, and the dispatcher keeps refusing.
    install("--hook-dispatcher", "on")
    registry = json.loads((dispatch / "registry.json").read_text())
    damaged = json.loads(json.dumps(registry))
    damaged["per_hook"]["PreToolUse"] = []
    before = (home / ".claude/settings.json").read_bytes()
    for name in (f"registry.{registry['rev']}.json", "registry.json", "registry.json.bak"):
        (dispatch / name).write_text(json.dumps(damaged))
    assert install("--hook-dispatcher", "off", check=False).returncode != 0
    assert (home / ".claude/settings.json").read_bytes() == before


FLAKY = "import sys, time\nsys.stdin.read()\nprint(time.time_ns())\n"  # differs on every run: parity cannot pass


def test_a_sticky_run_whose_fixture_fails_keeps_the_fold(tmp_path: Path) -> None:
    """The coding-agents sync runs install-policy with no flag (sticky). 4e0dfdaa unfolded on a failed fixture, so
    one flake on a loaded machine dropped the dispatcher; now it reruns once, then keeps the installed fold."""
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
    """Two install-policy runs at once (the sync and a hand run) serialize on the per-home lock: afterwards every
    dispatcher entry in the settings names a rev whose registry file holds exactly that folded list."""
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
    """The parity flake: a fixture run while an install swaps guards read half of each. It now holds the per-home
    install lock (shared) and waits for an install that holds it."""
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
            assert run.poll() is None  # waiting on the lock
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            out = run.communicate(timeout=300)[0]
            assert run.returncode == 0, out
        finally:
            if run.poll() is None:
                os.killpg(run.pid, 9)
                run.wait(timeout=30)
