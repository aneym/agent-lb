"""bash-dispatch: one PreToolUse(Bash) hook process in place of ~15, same decisions (2026-10-07 offload).

The dispatcher runs as a subprocess on real hook scripts; the installer runs through its CLI on a scratch home.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DISPATCH = ROOT / "config" / "coding-agents" / "hooks" / "bash-dispatch.py"
POLICY = ROOT / "config" / "coding-agents" / "install-policy.py"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def hook(tmp_path: Path, name: str, body: str, timeout: float | None = None) -> dict:
    script = tmp_path / name
    script.write_text("#!/bin/sh\n" + body + "\n")
    script.chmod(0o755)
    entry = {"type": "command", "command": f'"{script}"'}
    if timeout:
        entry["timeout"] = timeout
    return entry


def emits(tmp_path: Path, name: str, data: dict, code: int = 0) -> dict:
    return hook(tmp_path, name, f"cat >/dev/null; echo '{json.dumps(data)}'; exit {code}")


def specific(**fields) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", **fields}}


def dispatch(tmp_path: Path, hooks, command: str = "ls", raw: str | None = None, direct: list | None = None):
    listed = tmp_path / "bash-hooks.json"
    listed.write_text(json.dumps(hooks))
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": direct or []}]}}))
    payload = raw if raw is not None else json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    env = {**os.environ, "BASH_DISPATCH_LIST": str(listed), "BASH_DISPATCH_SETTINGS": str(settings)}
    return subprocess.run([sys.executable, str(DISPATCH)], input=payload, capture_output=True, text=True, env=env,
                          timeout=60)


def out(done) -> dict:
    return json.loads(done.stdout) if done.stdout.strip() else {}


def test_any_block_blocks_with_every_blocker_message(tmp_path):
    hooks = [hook(tmp_path, "a", "echo first >&2; exit 2"), hook(tmp_path, "b", "exit 0"),
             emits(tmp_path, "c", specific(updatedInput={"command": "x"})),
             hook(tmp_path, "d", "echo second >&2; exit 2")]
    done = dispatch(tmp_path, hooks)
    assert done.returncode == 2
    assert done.stderr == "first\nsecond\n"


def test_box_offload_rewrite_wins_over_rtk(tmp_path):
    rtk = emits(tmp_path, "rtk", specific(updatedInput={"command": "rtk pytest"}))
    box = emits(tmp_path, "box-offload-hook", specific(updatedInput={"command": "box-exec -- pytest"}))
    rtk["command"] += " # rtk hook claude"
    done = dispatch(tmp_path, [rtk, box], "pytest")
    assert done.returncode == 0
    assert out(done)["hookSpecificOutput"]["updatedInput"]["command"] == "box-exec -- pytest"


def test_deny_wins_over_a_rewrite(tmp_path):
    hooks = [emits(tmp_path, "r", specific(updatedInput={"command": "y"})),
             emits(tmp_path, "d", specific(permissionDecision="deny", permissionDecisionReason="no"))]
    assert out(dispatch(tmp_path, hooks))["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("decisions, want", [(["allow", "ask"], "ask"), (["ask", "defer", "allow"], "defer"),
                                             (["allow", "deny", "ask"], "deny")])
def test_the_strongest_permission_decision_wins(tmp_path, decisions, want):
    hooks = [emits(tmp_path, f"h{i}", specific(permissionDecision=d)) for i, d in enumerate(decisions)]
    assert out(dispatch(tmp_path, hooks))["hookSpecificOutput"]["permissionDecision"] == want


@pytest.mark.parametrize("code", [1, 3, 127])
def test_a_deny_printed_with_a_nonzero_exit_still_denies(tmp_path, code):
    done = dispatch(tmp_path, [emits(tmp_path, "d", specific(permissionDecision="deny"), code=code)])
    assert (done.returncode, out(done)["hookSpecificOutput"]["permissionDecision"]) == (0, "deny")


def test_malformed_output_from_one_hook_keeps_another_hooks_deny(tmp_path):
    hooks = [emits(tmp_path, "bad", {"hookSpecificOutput": "invalid"}),
             emits(tmp_path, "d", specific(permissionDecision="deny"))]
    done = dispatch(tmp_path, hooks)
    assert out(done)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_stop_and_every_context_survive_a_rewrite(tmp_path):
    hooks = [emits(tmp_path, "r", specific(updatedInput={"command": "y"}, additionalContext="one")),
             emits(tmp_path, "s", {"continue": False, "stopReason": "guard stopped"}),
             emits(tmp_path, "c", specific(additionalContext="two"))]
    data = out(dispatch(tmp_path, hooks))
    assert (data["continue"], data["stopReason"]) == (False, "guard stopped")
    assert data["hookSpecificOutput"]["updatedInput"] == {"command": "y"}
    assert data["hookSpecificOutput"]["additionalContext"] == "one\n\ntwo"


def test_a_timed_out_hook_does_not_block_and_its_children_die(tmp_path):
    pid = tmp_path / "pid"
    started = time.monotonic()
    done = dispatch(tmp_path, [hook(tmp_path, "slow", f"sleep 30 & echo $! > {pid}; wait; exit 2", timeout=0.5)])
    assert (done.returncode, done.stdout) == (0, "")
    assert time.monotonic() - started < 10
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid.read_text()), 0)


def test_a_block_is_kept_while_a_slower_hook_finishes_within_its_timeout(tmp_path):
    hooks = [hook(tmp_path, "block", "echo denied >&2; exit 2"), hook(tmp_path, "slow", "sleep 2", timeout=120)]
    assert dispatch(tmp_path, hooks).returncode == 2


def test_the_dispatcher_registration_outlasts_its_slowest_hook():
    policy = load(POLICY, "install_policy")
    assert policy.bash_dispatch_hook([{"type": "command", "command": "a", "timeout": 120}])["timeout"] > 120
    assert policy.bash_dispatch_hook([{"type": "command", "command": "a"}])["timeout"] > 600


@pytest.mark.parametrize("contents", [None, "{}", "[]", '[{"type":"command","command":null}]',
                                      '[{"type":"command","command":"x","args":["y"]}]', "not json"])
def test_a_missing_or_invalid_list_refuses_every_call(tmp_path, contents):
    listed = tmp_path / "list.json"
    if contents is not None:
        listed.write_text(contents)
    env = {**os.environ, "BASH_DISPATCH_LIST": str(listed)}
    done = subprocess.run([sys.executable, str(DISPATCH)], input="{}", capture_output=True, text=True, env=env)
    assert done.returncode == 2
    assert "install-policy.py" in done.stderr


def test_a_hook_still_registered_directly_is_not_run_twice(tmp_path):
    marker = tmp_path / "ran"
    entry = hook(tmp_path, "once", f"touch {marker}")
    dispatch(tmp_path, [entry], direct=[entry])
    assert not marker.exists()


def test_a_conditional_direct_copy_does_not_stand_in_for_the_listed_hook(tmp_path):
    entry = hook(tmp_path, "railway-vars-guard.sh", "echo denied >&2; exit 2")
    done = dispatch(tmp_path, [entry], "railway variables", direct=[{**entry, "if": "Bash(git *)"}])
    assert done.returncode == 2


@pytest.mark.parametrize("raw", ["not json", json.dumps({"tool_name": "Bash", "tool_input": {"cmd": "x"}})])
def test_an_unreadable_payload_runs_every_gated_hook(tmp_path, raw):
    entry = hook(tmp_path, "link-cli-guard.sh", "echo seen >&2; exit 2")
    assert dispatch(tmp_path, [entry], raw=raw).returncode == 2


@pytest.mark.parametrize("marker, hits, misses", [
    ("link-cli-guard.sh", ["link-cli auth status", "lin'k'-cli x"], ["ls"]),
    ("no-direct-merge.py", ["gh pr merge 1", 'gh pr me"rge" 1', "echo $'\\x6d'"], ["git status"]),
    ("railway-vars-guard.sh", ["railway variables"], ["git log"]),
    ("stash-guard", ["git stash"], ["git status"]),
    ("rm-cd-rewrite.py", ["cd /tmp && rm x"], ["rm x", "cd /tmp"]),
    ("agent-lb-bootout-guard.sh", ["launchctl kickstart gui/501/com.aneyman.agent-lb"], ["launchctl list"]),
    ("herdr-shell-host-guard.py", ["open -a HerdrShell"], ["ls"]),
])
def test_gates_skip_a_pinned_guard_only_when_its_trigger_word_is_absent(monkeypatch, marker, hits, misses):
    module = load(DISPATCH, "bash_dispatch")
    monkeypatch.setattr(module, "guard_digest", lambda command, mark: module.PINS[mark])
    for command, want in [(c, True) for c in hits] + [(c, False) for c in misses]:
        payload = {"tool_name": "Bash", "tool_input": {"command": command}}
        assert module.needed(f"/x/{marker}", json.dumps(payload), payload) is want, command


def test_a_guard_that_changed_since_its_pin_always_runs(tmp_path):
    module = load(DISPATCH, "bash_dispatch")
    guard = tmp_path / "railway-vars-guard.sh"
    guard.write_text("#!/bin/sh\nexit 2\n")
    payload = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
    assert module.needed(f'"{guard}"', json.dumps(payload), payload) is True


@pytest.mark.parametrize("payload", [
    {"tool_name": "Bash", "tool_input": {"command": "tree /"}},
    {"tool_name": "Bash", "tool_input": {"command": "du /"}},
])
def test_wide_scan_always_runs(payload):
    module = load(DISPATCH, "bash_dispatch")
    assert module.needed('"$HOME/.claude/hooks/wide-scan-guard.sh"', json.dumps(payload), payload) is True


def test_the_herdr_gate_reads_every_field_its_guard_reads(monkeypatch):
    module = load(DISPATCH, "bash_dispatch")
    monkeypatch.setattr(module, "guard_digest", lambda command, mark: module.PINS[mark])
    payload = {"tool_name": "Bash", "tool_input": {"command": "", "cmd": "open -a HerdrShell"}}
    assert module.needed("/x/herdr-shell-host-guard.py", json.dumps(payload), payload) is True


def test_unknown_hooks_always_run():
    module = load(DISPATCH, "bash_dispatch")
    payload = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
    assert module.needed("rtk hook claude", json.dumps(payload), payload) is True
    assert module.needed("/x/box-offload-hook", json.dumps(payload), payload) is True


# Installer, through its CLI on a scratch home.

A = {"type": "command", "command": "guard-a", "timeout": 5}
B = {"type": "command", "command": "guard-b"}
PROMPT = {"type": "prompt", "prompt": "check"}
CONDITIONAL = {"type": "command", "command": "guard-if", "if": "Bash(rm *)"}
EXEC = {"type": "command", "command": "/bin/sh", "args": ["-c", "exit 2"]}
READ = {"matcher": "Read", "hooks": [{"type": "command", "command": "read-hook"}]}


@pytest.fixture
def installer(tmp_path):
    source = tmp_path / "policy-source"
    shutil.copytree(ROOT / "config/coding-agents", source)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    env = os.environ | {"HOME": str(home), "AGENT_LB_URL": "http://127.0.0.1:1",
                        "ROUTE_MODELS_CACHE": str(tmp_path / "models-cache.json")}
    for key in ("ROUTE_FIXTURE_DIR", "ROUTE_TABLE"):
        env.pop(key, None)

    class Installer:
        settings = home / ".claude/settings.json"
        listed = home / ".agent-lb/managed/coding-agents/bash-hooks.json"

        def run(self, *options):
            return subprocess.run([sys.executable, str(source / "install-policy.py"), "--home", str(home), *options],
                                  env=env, capture_output=True, text=True, timeout=60)

        def write(self, groups):
            self.settings.write_text(json.dumps({"hooks": {"PreToolUse": groups}}))

        def groups(self):
            return json.loads(self.settings.read_text())["hooks"]["PreToolUse"]

        def bash(self):
            return [h for g in self.groups() if g.get("matcher") == "Bash" for h in g["hooks"]]

    return Installer()


def is_dispatcher(entry):
    return "bash-dispatch.py" in entry.get("command", "")


def test_install_folds_plain_hooks_and_uninstall_puts_them_back(installer):
    installer.write([{"matcher": "Bash", "hooks": [A, PROMPT, CONDITIONAL, EXEC]}, READ, {"matcher": "Bash",
                                                                                          "hooks": [B]}])
    assert installer.run().returncode == 0
    assert json.loads(installer.listed.read_text()) == [A, B]
    bash = installer.bash()
    assert [h for h in bash if not is_dispatcher(h)] == [PROMPT, CONDITIONAL, EXEC]
    assert is_dispatcher(bash[0]) and bash[0]["timeout"] > 600
    assert READ in installer.groups()
    assert installer.run().returncode == 0
    assert json.loads(installer.listed.read_text()) == [A, B]
    assert installer.run("--uninstall").returncode == 0
    assert installer.bash() == [A, B, PROMPT, CONDITIONAL, EXEC]
    assert not installer.listed.exists()


def test_a_later_registration_joins_after_and_a_reregistered_one_replaces_in_place(installer):
    installer.write([{"matcher": "Bash", "hooks": [A, B]}])
    installer.run()
    groups = installer.groups()
    newer_a = {**A, "timeout": 50}
    later = {"type": "command", "command": "guard-c"}
    groups.append({"matcher": "Bash", "hooks": [newer_a, later]})
    installer.write(groups)
    assert installer.run().returncode == 0
    assert json.loads(installer.listed.read_text()) == [newer_a, B, later]
    assert installer.run("--uninstall").returncode == 0
    assert installer.bash() == [newer_a, B, later]


@pytest.mark.parametrize("option", [(), ("--uninstall",)])
def test_a_missing_list_with_the_dispatcher_registered_aborts(installer, option):
    installer.write([{"matcher": "Bash", "hooks": [A]}])
    installer.run()
    installer.listed.unlink()
    before = installer.settings.read_text()
    done = installer.run(*option)
    assert done.returncode != 0 and "hook list is missing" in done.stderr
    assert installer.settings.read_text() == before
