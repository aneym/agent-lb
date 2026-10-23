from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.floor.hook_dispatcher_guard_support import (
    DANGEROUS,
    RAILWAY,
)
from tests.floor.hook_dispatcher_support import (
    DISPATCH_BASH,
    PRETTIER,
    SOURCE,
    bash_groups,
    decision,
    hooks_of,
    installer,
    make_home,
    per_hook,
    run_hook,
)
from tests.floor.hook_dispatcher_support import kill_spawned_hooks as kill_spawned_hooks


def test_the_fold_drops_the_global_prettier_hook(tmp_path: Path) -> None:

    import hashlib

    assert hashlib.sha256(PRETTIER.encode()).hexdigest().startswith("55c67a61")
    home, settings = make_home(tmp_path)
    settings["hooks"]["PostToolUse"].insert(
        0, {"matcher": "Edit|Write|MultiEdit", "hooks": [{"type": "command", "command": PRETTIER, "timeout": 10}]}
    )
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
    assert (
        refused.returncode == 2
        and "BLOCKED (floor: secrets and protected credentials): recursive content reads must stay inside one repository"
        in refused.stderr
    ), refused
    allowed = run_hook(f'"{guard}"', call | {"tool_input": {"command": "rg -n needle src"}}, env)
    assert allowed.returncode == 0 and not allowed.stdout, allowed

    mirror = home / ".agents/policy/coding-agents"
    assert os.access(mirror / "hooks/wide-scan-guard.sh", os.X_OK)
    rollback = subprocess.run(
        [sys.executable, str(mirror / "install-policy.py"), "--home", str(home), "--hook-dispatcher", "off"],
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert rollback.returncode == 0, rollback.stdout + rollback.stderr

    install("--uninstall")
    assert guard.read_bytes() == (SOURCE / "hooks/wide-scan-guard.sh").read_bytes() and os.access(guard, os.X_OK)
    assert not (home / ".agent-lb/managed/coding-agents/wide-scan-guard").exists()
    refused = run_hook(f'"{guard}"', call | {"tool_input": {"command": "rg needle /"}}, env)
    assert (
        refused.returncode == 2
        and "BLOCKED (floor: secrets and protected credentials): recursive content reads must stay inside one repository"
        in refused.stderr
    ), refused


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
    hooks = [
        {"type": "command", "command": '"$HOME/.claude/hooks/wide-scan-guard.sh"'},
        {"type": "command", "command": '"$HOME/.claude/hooks/agent-lb-bootout-guard.sh"'},
    ]
    pinned = tmp_path / "pinned-registry.json"
    pinned.write_text(
        json.dumps(
            {
                "schema": 1,
                "entries": {"PreToolUse": {"Bash": hooks}},
                "inproc": [],
                "script_sha": registry["script_sha"],
            }
        )
    )
    call = {
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "hook_event_name": "PreToolUse",
        "cwd": str(home / "project"),
    }
    result = run_hook(
        DISPATCH_BASH, call, env | {"HOOK_DISPATCH_TRACE": str(trace), "HOOK_DISPATCH_REGISTRY": str(pinned)}
    )
    assert decision(result) == "allow", result
    modes = [row.get("mode") for row in json.loads(trace.read_text().splitlines()[-1])["hooks"]]
    assert modes == ["skipped", "skipped"], modes


def test_install_refuses_a_floor_guard_that_is_not_a_direct_exec(tmp_path: Path) -> None:

    home, settings = make_home(tmp_path)
    install, _env = installer(tmp_path, home)
    settings["hooks"]["PreToolUse"].append(
        {
            "matcher": "Agent",
            "hooks": [
                {
                    "type": "command",
                    "command": "bash -c 'python3 \"$HOME/.agent-rails/factory-runtime/bin/rm-dynamic-deny\" || true'",
                }
            ],
        }
    )
    (home / ".claude/settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    before = (home / ".claude/settings.json").read_bytes()
    refused = install("--hook-dispatcher", "on", check=False)
    assert refused.returncode != 0 and "direct exec" in refused.stderr, refused.stdout + refused.stderr
    assert (home / ".claude/settings.json").read_bytes() == before
    assert not (home / ".claude/hooks/dispatch/registry.json").exists()
    settings["hooks"]["PreToolUse"].pop()
    settings["hooks"]["PreToolUse"].append({"matcher": "Bash", "hooks": [{"type": "command", "command": DANGEROUS}]})
    (home / ".claude/settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    install("--hook-dispatcher", "on")
    registry = json.loads((home / ".claude/hooks/dispatch/registry.json").read_text())

    commands = [hook["command"] for hooks in registry["entries"]["PreToolUse"].values() for hook in hooks] + [
        hook["command"] for group in hooks_of(home)["PreToolUse"] for hook in group["hooks"]
    ]
    assert '"$HOME/.claude/hooks/dangerous-command-guard.sh"' in commands
    assert DANGEROUS not in commands


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
    settings["hooks"]["PreToolUse"].append(
        {
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": '"$HOME/.claude/hooks/railway-vars-guard.sh"', "timeout": 10}],
        }
    )
    (home / ".claude/settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    install, _env = installer(tmp_path, home)
    folded = install("--hook-dispatcher", "on", check=False)
    assert folded.returncode == 0, folded.stdout[-2000:] + folded.stderr[-2000:]
    registry = json.loads((home / ".claude/hooks/dispatch/registry.json").read_text())
    assert '"$HOME/.claude/hooks/railway-vars-guard.sh"' in [
        hook["command"] for hook in registry["entries"]["PreToolUse"]["Bash"]
    ]
    assert os.access(home / ".claude/hooks/railway-vars-guard.sh", os.X_OK)
