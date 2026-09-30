"""Launcher boundary: rollout tags reach the dry run without changing launch args.

Existing proxy tests own header delivery; these cases own installed policy and
registry resolution, including exclusions and fail-open malformed input.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[2] / "clients" / "claude-lb-launch"


@pytest.mark.parametrize(
    ("stage", "tab", "kind", "extra", "args", "intent", "lane", "reason"),
    [
        ("canary", "w5H:tC8", {"kind": "lane", "lane": "build"}, {}, [], "lane-tab", "build", "canary"),
        ("canary", "w5H:other", {}, {}, [], "none", "none", "outside canary"),
        ("canary", "w5H:other", {"kind": "lane", "lane": "selected"}, {}, [], "lane-tab", "selected", "canary"),
        ("all", "w5H:other", None, {}, [], "lane-tab", "w5H:other", "all"),
        ("all", "w5H:tC8", {"lane": "bad\u0000lane"}, {}, [], "lane-tab", "w5H:tC8", "all"),
        ("all", "w5H:tC8", {"lane": "bad—lane"}, {}, [], "lane-tab", "w5H:tC8", "all"),
        ("all", "w5H:tC8", {"lane": "bad\nlane"}, {}, [], "lane-tab", "w5H:tC8", "all"),
        ("all", "w5H:" + "x" * 125, None, {}, [], "none", "none", "invalid herdr tab"),
        ("all", "w5H:other", {"kind": "orchestrator", "lane": "factory"}, {}, [], "orchestrator", "factory", "all"),
        ("all", "w5H:other", {"kind": "ephemeral"}, {}, [], "none", "none", "excluded registry kind"),
        ("all", "w5H:other", {"kind": "background"}, {}, [], "none", "none", "excluded registry kind"),
        (
            "all", "w5H:other", {}, {"AGENT_LB_INTENT": "implement", "AGENT_LB_LANE": "explicit"},
            [], "implement", "explicit", "explicit intent",
        ),
        ("all", "w5H:other", {}, {"AGENT_LB_INTENT": "", "AGENT_LB_LANE": "kept"}, [], "lane-tab", "kept", "all"),
        ("all", "w5H:other", {}, {}, ["-p", "hello"], "none", "none", "print mode"),
        ("all", "w5H:other", {}, {}, ["--print", "hello"], "none", "none", "print mode"),
        ("all", "w5H:other", {}, {}, ["--print=hello"], "none", "none", "print mode"),
        ("all", "w5H:other", {}, {"CLAUDECODE": "1"}, [], "none", "none", "child process"),
        ("all", None, {}, {}, [], "none", "none", "no herdr tab"),
        ("off", "w5H:tC8", {}, {}, [], "none", "none", "rollout off"),
        (None, "w5H:tC8", {}, {}, [], "none", "none", "rollout off"),
        ("malformed", "w5H:tC8", {}, {}, [], "none", "none", "policy or registry unreadable"),
        ("all", "w5H:tC8", "malformed", {}, [], "none", "none", "policy or registry unreadable"),
        ("all", "w5H:tC8", [], {}, [], "none", "none", "policy or registry unreadable"),
    ],
)
def test_launch_rollout_dry_run(tmp_path, stage, tab, kind, extra, args, intent, lane, reason):
    table = tmp_path / ".agent-lb" / "managed" / "coding-agents" / "routing-table.json"
    table.parent.mkdir(parents=True)
    policy = {} if stage is None else {"stand_in_rollout": {"stage": stage, "tabs": ["w5H:tC8"], "lanes": ["selected"]}}
    table.write_text("{" if stage == "malformed" else json.dumps({"policy": policy}))
    if tab and kind is not None:
        registry = tmp_path / ".agent-rails" / "workflows" / "kinds" / f"{tab.replace(':', '_')}.json"
        registry.parent.mkdir(parents=True)
        registry.write_text("{" if kind == "malformed" else json.dumps(kind))
    env = {
        "PATH": os.defpath,
        "HOME": str(tmp_path),
        "CLAUDE_LB_DRY_RUN": "1",
        "CLAUDE_LB_DISABLE": "1",
        **extra,
    }
    if tab:
        env["HERDR_TAB_ID"] = tab
    completed = subprocess.run(
        [sys.executable, str(LAUNCHER), *args], env=env, capture_output=True, text=True, timeout=5,
    )
    assert completed.returncode == 0, completed.stderr
    assert f"agent-lb tags: intent={intent} lane={lane} reason={reason}" in completed.stderr
    assert completed.stdout.splitlines()[0].startswith("claude ")


@pytest.mark.parametrize("configured", [False, True])
def test_launch_reads_fallback_or_configured_table(tmp_path, configured):
    table = tmp_path / ".agents" / "policy" / "coding-agents" / "routing-table.json"
    table.parent.mkdir(parents=True)
    table.write_text(json.dumps({"policy": {"stand_in_rollout": {"stage": "all"}}}))
    env = {
        "PATH": os.defpath,
        "HOME": str(tmp_path),
        "HERDR_TAB_ID": "w5H:tC8",
        "CLAUDE_LB_DRY_RUN": "1",
        "CLAUDE_LB_DISABLE": "1",
    }
    if configured:
        env["ROUTE_TABLE"] = str(table)
        managed = tmp_path / ".agent-lb" / "managed" / "coding-agents" / "routing-table.json"
        managed.parent.mkdir(parents=True)
        managed.write_text("{")
    completed = subprocess.run([sys.executable, str(LAUNCHER)], env=env, capture_output=True, text=True, timeout=5)
    assert completed.returncode == 0, completed.stderr
    assert "intent=lane-tab lane=w5H:tC8 reason=all" in completed.stderr


@pytest.mark.parametrize("failure", ["home", "timeout"])
def test_launch_lookup_failure_proceeds_untagged(monkeypatch, capsys, failure):
    loader = importlib.machinery.SourceFileLoader("launch_rollout_failure", str(LAUNCHER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    launcher = importlib.util.module_from_spec(spec)
    loader.exec_module(launcher)
    monkeypatch.setattr(launcher.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(
        args[0], 0, '{"result":{"tab":{"label":""}}}', "",
    ))
    monkeypatch.setenv("HERDR_TAB_ID", "w5H:tC8")
    monkeypatch.setenv("CLAUDE_LB_DRY_RUN", "1")
    monkeypatch.setenv("CLAUDE_LB_DISABLE", "1")
    for name in ("CLAUDECODE", "AGENT_LB_INTENT", "AGENT_LB_LANE", "CLAUDE_LB_REQUIRE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "argv", [str(LAUNCHER)])
    if failure == "home":
        def unavailable_home():
            raise RuntimeError("home unavailable")
        monkeypatch.setattr(launcher.Path, "home", unavailable_home)
        reason = "policy or registry unreadable"
    else:
        class StalledReader:
            def __init__(self, **kwargs):
                pass

            def start(self):
                pass

            def join(self, timeout):
                pass

            def is_alive(self):
                return True
        monkeypatch.setattr(launcher.threading, "Thread", StalledReader)
        reason = "policy lookup timed out"
    launcher.main()
    output = capsys.readouterr()
    assert output.out.startswith("claude ")
    assert f"intent=none lane=none reason={reason}" in output.err
    assert "AGENT_LB_INTENT" not in os.environ
    assert "AGENT_LB_LANE" not in os.environ
