"""Scenario (2026-09-29, recruiter desktop 06:05Z): a headless turn that resumes a Claude Code
session must not get an injected --session-id. Claude Code exits 1 on --session-id with
--resume/--continue (without --fork-session), which broke every second desktop chat turn."""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path

import pytest


def load():
    path = Path(__file__).resolve().parents[2] / "clients" / "claude-lb-launch"
    loader = importlib.machinery.SourceFileLoader("claude_lb_launch_resume_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


BASE = ["claude", "-p", "hi"]


@pytest.mark.parametrize("argv, expected_id", [
    (["-p", "hi", "--resume", "abc-123"], "abc-123"),
    (["-p", "hi", "-r", "abc-123"], "abc-123"),
    (["-p", "hi", "--resume=abc-123"], "abc-123"),
])
def test_a_resumed_turn_keeps_its_session_and_gets_no_session_id(argv, expected_id):
    launcher = load()
    command, claude_session_id = launcher.claude_session_args(argv, [*BASE, *argv[2:]])
    assert "--session-id" not in command
    assert claude_session_id == expected_id


@pytest.mark.parametrize("flag", ["--continue", "-c"])
def test_a_continued_turn_gets_no_session_id_and_no_known_id(flag):
    launcher = load()
    argv = ["-p", "hi", flag]
    command, claude_session_id = launcher.claude_session_args(argv, [*BASE, flag])
    assert "--session-id" not in command
    assert claude_session_id is None


def test_a_first_turn_still_gets_a_fresh_session_id():
    launcher = load()
    command, claude_session_id = launcher.claude_session_args(["-p", "hi"], list(BASE))
    assert command[-2:] == ["--session-id", claude_session_id]
    assert len(claude_session_id) == 36


def test_an_explicit_session_id_is_kept_as_is():
    launcher = load()
    argv = ["-p", "hi", "--session-id", "given-1"]
    command, claude_session_id = launcher.claude_session_args(argv, [*BASE, "--session-id", "given-1"])
    assert command.count("--session-id") == 1 and claude_session_id == "given-1"


def test_auto_resume_path_runs_a_resumed_turn_without_session_id(monkeypatch):
    launcher = load()
    seen = []
    monkeypatch.setattr(launcher, "run_supervised",
                        lambda command, activity, stall, session_id=None: seen.append((command, session_id)) or 0)
    argv = ["-p", "hi", "--resume", "abc-123"]
    assert launcher.run_headless_with_resume([*BASE, "--resume", "abc-123"], argv, "lb-1", "opus", "q") == 0
    assert seen == [([*BASE, "--resume", "abc-123"], "abc-123")]


def test_a_continued_turn_that_fails_is_not_auto_resumed_without_an_id(monkeypatch):
    launcher = load()
    calls = []
    monkeypatch.setattr(launcher, "run_supervised",
                        lambda command, activity, stall, session_id=None: calls.append(command) or 1)
    monkeypatch.setattr(launcher, "claim_session_route", lambda *a, **k: (None, None, 1.0))
    monkeypatch.setattr(launcher, "should_wait_for_reset", lambda *a, **k: True)
    monkeypatch.setattr(launcher, "wait_until_reset", lambda *a, **k: None)
    argv = ["-p", "hi", "--continue"]
    assert launcher.run_headless_with_resume([*BASE, "--continue"], argv, "lb-1", "opus", "q") == 1
    assert len(calls) == 1
