from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "lb-restart"


def _load_lb_restart() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("lb_restart_under_test", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class _Standby:
    pid = 424242

    def poll(self) -> None:
        return None


def _fake_swap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pending_polls: dict[int, list[int]]) -> tuple:
    """lb-restart with every process, launchd and front action replaced by a recorder.

    pending_polls maps a port to the pending-flow counts its /internal/oauth/pending
    returns on successive polls (the last value repeats).
    """
    lb = _load_lb_restart()
    events: list[str] = []
    polls: dict[int, int] = {}

    def http_get(port: int, path: str, timeout: float = 3.0) -> tuple[int, bytes]:
        if path != "/internal/oauth/pending":
            return 200, b"{}"
        counts = pending_polls.get(port, [0])
        n = counts[min(polls.get(port, 0), len(counts) - 1)]
        polls[port] = polls.get(port, 0) + 1
        return 200, json.dumps({"pending": n, "ages_seconds": [30.0] * n}).encode()

    def set_preferred(port: int) -> None:
        events.append(f"front->{port}")

    def restart_primary(reload_plist: bool, old_pid: int | None, bound: float) -> None:
        events.append("restart-primary")

    alive = {"old": True}

    def launchd_pid(label: str) -> int:
        return 111 if alive["old"] else 222

    def pid_alive(pid: int | None) -> bool:
        if pid == 111 and alive["old"]:
            alive["old"] = False  # the old primary exits once asked
            return True
        return False

    monkeypatch.setattr(lb, "SYNC_LOG", tmp_path / "sync.log")
    monkeypatch.setattr(lb, "OAUTH_POLL_S", 0.0, raising=False)
    monkeypatch.setattr(lb, "http_get", http_get)
    monkeypatch.setattr(lb, "healthy", lambda port: True)
    monkeypatch.setattr(lb, "graceful_seconds", lambda: 1)
    monkeypatch.setattr(lb, "exit_timeout", lambda: 1)
    monkeypatch.setattr(lb, "front_understands_preference", lambda: True)
    monkeypatch.setattr(lb, "front_routes_to", lambda port, timeout=5.0: True)
    monkeypatch.setattr(lb, "stop_leftover_standby", lambda drain, hold_for_oauth=None: None)
    monkeypatch.setattr(lb, "set_preferred", set_preferred)
    monkeypatch.setattr(lb, "start_standby", lambda drain: events.append("start-standby") or _Standby())
    monkeypatch.setattr(lb, "restart_primary", restart_primary)
    monkeypatch.setattr(lb, "launchd_pid", launchd_pid)
    monkeypatch.setattr(lb, "pid_alive", pid_alive)
    monkeypatch.setattr(lb, "established", lambda port: 0)
    monkeypatch.setattr(lb, "listener_pid", lambda port: None)
    monkeypatch.setattr(lb, "spawn_reaper", lambda pid, drain: None)
    monkeypatch.setattr(lb.os, "killpg", lambda pid, sig: events.append("stop-standby"))
    monkeypatch.setattr(lb.time, "sleep", lambda seconds: None)
    return lb, events, polls


def _args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "reason": "test",
        "src": None,
        "files": [],
        "backup": None,
        "reload_plist": False,
        "check": False,
        "force": False,
        "oauth_wait": 600.0,
        "lock_wait": 1.0,
        "boot_timeout": 1.0,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_pending_sign_in_holds_each_front_move_until_it_finishes(monkeypatch, tmp_path) -> None:
    # A sign-in on the primary, then one started on the standby during the swap.
    pending = {2457: [1, 1, 1, 0], 2459: [1, 0]}
    lb, events, polls = _fake_swap(monkeypatch, tmp_path, pending)

    assert lb.run(_args()) == 0

    assert events == ["front->2457", "start-standby", "front->2459", "restart-primary", "front->2457", "stop-standby"]
    assert polls.get(2457, 0) >= 4, "the primary's sign-in was not waited out before the front moved"
    assert polls.get(2459, 0) >= 2, "the standby's sign-in was not waited out before the front moved back"
    log = (tmp_path / "sync.log").read_text()
    assert "waiting on 1 oauth flow(s) on :2457" in log
    assert "waiting on 1 oauth flow(s) on :2459" in log
    assert "lb-restart ok" in log


def test_force_swaps_without_asking_about_sign_ins(monkeypatch, tmp_path) -> None:
    lb, events, polls = _fake_swap(monkeypatch, tmp_path, {2457: [1], 2459: [1]})

    assert lb.run(_args(force=True)) == 0

    assert events == ["front->2457", "start-standby", "front->2459", "restart-primary", "front->2457", "stop-standby"]
    assert polls == {}
    assert "oauth" not in (tmp_path / "sync.log").read_text()


def test_wait_is_bounded_and_logged(monkeypatch, tmp_path) -> None:
    lb, events, _ = _fake_swap(monkeypatch, tmp_path, {2457: [2]})

    assert lb.run(_args(oauth_wait=0.0)) == 0

    assert "front->2459" in events
    log = (tmp_path / "sync.log").read_text()
    assert "waiting on 2 oauth flow(s) on :2457" in log
    assert "proceeding with 2 oauth flow(s) still pending on :2457" in log
