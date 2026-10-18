from __future__ import annotations

import json
import os
import shutil
import signal
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from tests.floor.hook_dispatcher_support import (
    DISPATCH_BASH,
    FAKE_RTK,
    QUIET_RTK,
    SOURCE,
    TIMER_RTK,
    dispatcher_home,
    parity_module,
    processes_naming,
    rewriter_home,
    run_hook,
    run_owned,
)
from tests.floor.hook_dispatcher_support import kill_spawned_hooks as kill_spawned_hooks


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
    tree = (
        'python3 -c \'import subprocess, sys; [subprocess.run([sys.executable, "-c", "pass"]) '
        "for _ in range(2)]'; exit 0"
    )
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
    _home, env = dispatcher_home(
        tmp_path / "e", [{"type": "command", "command": "sleep 0.2"}, {"type": "command", "command": "sleep 0.3"}]
    )
    spawned = observe(DISPATCH_BASH, call, env, str(tmp_path), 30)
    assert spawned["observed"] and spawned["hook_layer"] == 3 and spawned["processes"] >= 3, spawned


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


def test_a_hook_child_left_running_or_stopped_is_killed_after_the_parity_run(tmp_path: Path) -> None:

    from importlib.machinery import SourceFileLoader

    parity = SourceFileLoader("hook_dispatch_parity", str(SOURCE / "hooks/hook-dispatch-parity.py")).load_module()
    marker = str(tmp_path / "leftover-marker")
    command = (
        f"sh -c 'kill -STOP $$; sleep 300 # {marker}' </dev/null >/dev/null 2>&1 & "
        f"sleep 300 {marker} </dev/null >/dev/null 2>&1 & exit 0"
    )
    result = parity.run_shell(command, b"{}", dict(os.environ), str(tmp_path), 30)
    assert result["code"] == 0 and not result["timed_out"]
    deadline = time.monotonic() + 5
    while processes_naming(marker) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert processes_naming(marker) == []
