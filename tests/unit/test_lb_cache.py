from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

LB_CACHE = Path(__file__).resolve().parents[2] / "clients" / "lb-cache"


def load_lb_cache():
    loader = importlib.machinery.SourceFileLoader("lb_cache_test", str(LB_CACHE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_account_switches_warn_but_cache_rewrites_still_alert() -> None:
    lb_cache = load_lb_cache()
    metrics = {"ccgpt-bridge": {"rewrite_rate": 0.01, "steady_turns": 700,
                                "switch_rate": 0.5, "multi_request_sessions": 27},
               "codex-cli": {"rewrite_rate": 0.5, "steady_turns": 300,
                             "switch_rate": 0.0, "multi_request_sessions": 20}}

    verdict, checks = lb_cache.evaluate(metrics, [], datetime.now(timezone.utc))

    states = {c["check"]: c["state"] for c in checks}
    assert states["ccgpt-bridge.account_switch"] == "WARN"
    assert states["codex-cli.rewrite"] == "ALERT"
    assert verdict == "ALERT"


def test_query_uses_homebrew_psql_when_path_has_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # cache-watch runs lb-cache from launchd, whose PATH (/usr/bin:/bin) has no psql.
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(os, "access", lambda path, mode: path == "/opt/homebrew/bin/psql")
    lb_cache = load_lb_cache()
    calls = []
    monkeypatch.setattr(lb_cache.subprocess, "run",
                        lambda argv, **kw: calls.append(argv) or subprocess.CompletedProcess(argv, 0, "1|2\n", ""))

    assert lb_cache.query("SELECT 1") == [["1", "2"]]
    assert calls[0][0] == "/opt/homebrew/bin/psql"
