"""lb-restart --sandbox: the guard and the constant rebinding.

Unit level on purpose: the guard is a decision table with many edge cases, and
a CLI test that got past a broken guard could restart the live agent-lb. The
real restart of a sandbox is proven by scripts/lb-sandbox-check on Studio.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import plistlib
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "lb-restart"


def _load() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("lb_restart_sandbox_under_test", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _config(home: Path, **overrides) -> dict:
    root = home / ".agent-lb" / "sandboxes" / "r1"
    config = {
        "root": str(root),
        "label": "com.agent-lb.drill.sbx-r1",
        "plist": str(root / "launchd" / "com.agent-lb.drill.sbx-r1.plist"),
        "runtime": str(root / "runtime"),
        "state_dir": str(root / "state"),
        "front_port": 2470,
        "primary_port": 2471,
        "standby_port": 2472,
    }
    config.update(overrides)
    return config


def test_without_the_flag_every_constant_is_the_live_service() -> None:
    lb = _load()
    home = Path.home()
    assert lb.SANDBOX_CONFIG is None
    assert (lb.LABEL, lb.FRONT_LABEL) == ("com.aneyman.agent-lb", "com.aneyman.agent-lb-front")
    assert (lb.FRONT_PORT, lb.PRIMARY_PORT, lb.STANDBY_PORT) == (2455, 2457, 2459)
    assert lb.PLIST == home / "Library" / "LaunchAgents" / "com.aneyman.agent-lb.plist"
    assert lb.RUNTIME == home / ".agent-lb" / "runtime" / "agent-lb"
    assert lb.STATE_DIR == home / ".agent-lb" / "state"
    assert lb.LOCK_FILE == home / ".agent-lb" / "runtime" / "lb-restart.lock"


def test_sandbox_rebinds_every_path_under_the_root(tmp_path: Path) -> None:
    lb = _load()
    bound = lb.sandbox_bindings(_config(tmp_path), home=tmp_path)
    root = (tmp_path / ".agent-lb" / "sandboxes" / "r1").resolve()
    assert bound["LABEL"] == "com.agent-lb.drill.sbx-r1"
    assert bound["FRONT_LABEL"] is None
    assert (bound["FRONT_PORT"], bound["PRIMARY_PORT"], bound["STANDBY_PORT"]) == (2470, 2471, 2472)
    for name in (
        "RUNTIME",
        "BACKUPS",
        "SYNC_LOG",
        "STATE_DIR",
        "LOCK_FILE",
        "PREFERRED_FILE",
        "FRONT_STATE",
        "STANDBY_PIDFILE",
        "STANDBY_LOG",
        "WATCHDOG_PAUSE",
        "PLIST",
    ):
        assert root in Path(bound[name]).parents or Path(bound[name]) == root, name
    assert bound["PREFERRED_FILE"] == root / "state" / "front-preferred-port"
    assert bound["FRONT_STATE"] == root / "state" / "front.json"


@pytest.mark.parametrize(
    "overrides",
    [
        {"label": "com.aneyman.agent-lb"},
        {"label": "com.agent-lb.drill."},
        {"front_port": 2455},
        {"primary_port": 2457},
        {"standby_port": 2459},
        {"standby_port": 1455},
        {"front_port": 2469},
        {"front_port": 2600},
        {"standby_port": 2471},
        {"primary_port": "2471"},
        {"root": "relative/root"},
        {"root": "/tmp/agent-lb-sandbox"},
        {"plist": "/Users/someone/Library/LaunchAgents/com.aneyman.agent-lb.plist"},
        {"runtime": "../../../runtime/agent-lb"},
        {"state_dir": "/tmp/state"},
    ],
)
def test_sandbox_guard_refuses(tmp_path: Path, overrides: dict) -> None:
    lb = _load()
    with pytest.raises(lb.SandboxRefused):
        lb.sandbox_bindings(_config(tmp_path, **overrides), home=tmp_path)


def test_sandbox_root_must_be_below_sandboxes(tmp_path: Path) -> None:
    lb = _load()
    for root in (
        tmp_path / ".agent-lb" / "sandboxes",
        tmp_path / ".agent-lb" / "runtime",
        tmp_path / ".agent-lb" / "sandboxes" / "r1" / "nested",
    ):
        # Paths relative to the root, so only the root rule can refuse (not a path outside it).
        inside = {"plist": "launchd/x.plist", "runtime": "runtime", "state_dir": "state"}
        with pytest.raises(lb.SandboxRefused, match="not directly under|not under"):
            lb.sandbox_bindings(_config(tmp_path, root=str(root), **inside), home=tmp_path)
    config = _config(tmp_path)
    del config["standby_port"]
    with pytest.raises(lb.SandboxRefused):
        lb.sandbox_bindings(config, home=tmp_path)


@pytest.mark.parametrize(
    "planted",
    ["lb-restart.lock", "logs/sync.log", "state/front-preferred-port", "state/front-preferred-port.tmp", "backups"],
)
def test_sandbox_guard_refuses_symlinks_out_of_the_root(tmp_path: Path, planted: str) -> None:
    lb = _load()
    root = tmp_path / ".agent-lb" / "sandboxes" / "r1"
    live = tmp_path / ".agent-lb" / "runtime" / "live-target"
    live.parent.mkdir(parents=True)
    live.write_text("live state")
    link = root / planted
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(live)
    with pytest.raises(lb.SandboxRefused):
        lb.sandbox_bindings(_config(tmp_path), home=tmp_path)


@pytest.mark.parametrize("flags", [["--reload-plist"], ["--backup", "/tmp/x"], ["--from", "/tmp", "--files", "a"]])
def test_sandbox_flag_refuses_live_deploy_flags(tmp_path: Path, flags: list[str]) -> None:
    # The config does not exist, so a broken flag check still stops at the config guard, never at the live LB.
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--reason", "unit", "--sandbox", str(tmp_path / "missing.json"), *flags],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 2
    assert "--sandbox cannot be combined with" in proc.stderr


def test_sandbox_mode_signals_only_processes_of_its_root(tmp_path: Path) -> None:
    lb = _load()
    root = tmp_path / ".agent-lb" / "sandboxes" / "r1"
    root.mkdir(parents=True)
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", str(tmp_path / "elsewhere")])
    mine = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", str(root / "runtime")])
    try:
        assert lb.owned_by_sandbox(other.pid) is True  # no --sandbox: lb-restart behaves as before
        lb.__dict__.update(lb.sandbox_bindings(_config(tmp_path), home=tmp_path))
        assert lb.owned_by_sandbox(other.pid) is False
        assert lb.owned_by_sandbox(mine.pid) is True
    finally:
        for proc in (other, mine):
            proc.kill()
            proc.wait(10)


# ---------------------------------------------------------------- fix round 2 (review FAIL on 41a2f667)


@pytest.mark.parametrize(
    "planted",
    [
        "lb-restart.lock",
        "logs/sync.log",
        "state/front-preferred-port",
        "state/lb-standby.pid",
        "state/front.json",
        "watchdog.pause",
    ],
)
def test_sandbox_guard_refuses_hard_links_to_live_state(tmp_path: Path, planted: str) -> None:
    """Finding: an in-root lb-restart.lock hard-linked to the live front-preferred-port passed the path guard."""
    lb = _load()
    root = tmp_path / ".agent-lb" / "sandboxes" / "r1"
    live = tmp_path / ".agent-lb" / "state" / "front-preferred-port"
    live.parent.mkdir(parents=True)
    live.write_text("2457\n")
    (root / planted).parent.mkdir(parents=True, exist_ok=True)
    os.link(live, root / planted)
    with pytest.raises(lb.SandboxRefused):
        lb.sandbox_bindings(_config(tmp_path), home=tmp_path)
    assert live.read_text() == "2457\n"


def test_lock_taken_after_the_guard_never_truncates_a_hard_link(tmp_path: Path) -> None:
    """The same link planted after the guard ran: lock acquisition refuses instead of overwriting live state."""
    lb = _load()
    root = tmp_path / ".agent-lb" / "sandboxes" / "r1"
    root.mkdir(parents=True)
    live = tmp_path / ".agent-lb" / "state" / "front-preferred-port"
    live.parent.mkdir(parents=True)
    live.write_text("2457\n")
    lb.__dict__.update(lb.sandbox_bindings(_config(tmp_path), home=tmp_path))
    os.link(live, root / "lb-restart.lock")
    with pytest.raises(lb.SandboxRefused):
        with lb.Lock("unit", 1):
            pass
    (root / "state").mkdir()
    os.link(live, root / "state" / "front-preferred-port.tmp")
    with pytest.raises(lb.SandboxRefused):
        lb.set_preferred(2472)  # its temp file, hard-linked to live state, must not be truncated
    assert live.read_text() == "2457\n"
    os.unlink(root / "lb-restart.lock")
    with lb.Lock("unit", 1):  # control: a fresh lock of its own is taken
        pass


def _plist(root: Path, **overrides) -> dict:
    data = {
        "Label": "com.agent-lb.drill.sbx-r1",
        "ProgramArguments": [
            str(root / "runtime" / ".venv" / "bin" / "python"),
            str(root / "bin" / "lb-sandbox"),
            "_serve",
            str(root),
            "--host",
            "127.0.0.1",
            "--port",
            "2471",
        ],
        "WorkingDirectory": str(root / "runtime"),
        "EnvironmentVariables": {
            "HOME": str(root / "home"),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONPATH": str(root / "runtime"),
            "LB_SANDBOX_ROOT": str(root),
            "AGENT_LB_DATA_DIR": str(root / "data"),
            "AGENT_LB_DATABASE_URL": f"sqlite+aiosqlite:///{root / 'data' / 'store.db'}",
            "AGENT_LB_ENCRYPTION_KEY_FILE": str(root / "data" / "encryption.key"),
            "AGENT_LB_FEDERATION_PEER_URL": "http://127.0.0.1:2476",
            "AGENT_LB_DASHBOARD_AUTH_MODE": "trusted_header",
        },
    }
    env = overrides.pop("env", {})
    data.update(overrides)
    data["EnvironmentVariables"] = {**data["EnvironmentVariables"], **env}
    return data


@pytest.mark.parametrize(
    "overrides",
    [
        {"env": {"AGENT_LB_DATABASE_URL": "sqlite+aiosqlite:////h/.agent-lb/store.db"}},
        {"env": {"AGENT_LB_ENCRYPTION_KEY_FILE": "/h/.agent-lb/encryption.key"}},
        {"env": {"AGENT_LB_DATA_DIR": "/h/.agent-lb"}},
        {"env": {"HOME": "/h"}},
        {"env": {"AGENT_LB_FEDERATION_PEER_URL": "http://127.0.0.1:2455"}},
        {"env": {"AGENT_LB_FEDERATION_TOKEN": "x"}},
        {"ProgramArguments": ["/h/.agent-lb/runtime/agent-lb/.venv/bin/agent-lb", "--port", "2471"]},
        {"Label": "com.aneyman.agent-lb"},
        {"WorkingDirectory": "/h/.agent-lb/runtime/agent-lb"},
        # S2 fix round: a live port without a scheme, a path that climbs out, a key launchd runs instead.
        {"env": {"AGENT_LB_FEDERATION_PEER_HOST": "127.0.0.1:2457"}},
        {"env": {"AGENT_LB_PORT": "2455"}},
        {"env": {"AGENT_LB_FEDERATION_PUSH_PATH": "../../../../state/federation-push.json"}},
        {"env": {"AGENT_LB_CONVERSATION_ARCHIVE_DIR": "~/.agent-lb/conversation-archive"}},
        {"Program": "/h/.agent-lb/runtime/agent-lb/.venv/bin/agent-lb"},
        {"StandardOutPath": "/h/.agent-lb/agent-lb.log"},
    ],
)
def test_sandbox_plist_must_run_serve_against_its_own_store(tmp_path: Path, overrides: dict) -> None:
    """Finding: an in-root plist with a live DB URL or key path was trusted, so the standby booted on live."""
    lb = _load()
    root = (tmp_path / ".agent-lb" / "sandboxes" / "r1").resolve()
    lb.check_sandbox_plist(_plist(root), root, "com.agent-lb.drill.sbx-r1", 2471)  # control
    with pytest.raises(lb.SandboxRefused):
        lb.check_sandbox_plist(_plist(root, **overrides), root, "com.agent-lb.drill.sbx-r1", 2471)


# ---------------------------------------------------------------- S2 fix round (lb-restart only)


def _load_sandbox() -> ModuleType:
    path = SCRIPT.parent / "lb-sandbox"
    loader = importlib.machinery.SourceFileLoader("lb_sandbox_for_restart_parity", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_the_plist_lb_sandbox_writes_passes_the_restart_guard(tmp_path: Path) -> None:
    """Parity: the stricter guard must still accept the plist lb-sandbox start writes, or every
    sandbox restart refuses. A new env var in lb-sandbox that names a live port or an outside path fails here."""
    lb, lbs = _load(), _load_sandbox()
    root = (tmp_path / ".agent-lb" / "sandboxes" / "r1").resolve()
    root.mkdir(parents=True, mode=0o700)
    ports = {"front": 2470, "primary": 2471, "standby": 2472, "gate": 2473, "edge_anthropic": 2474, "edge_openai": 2475}
    primary, _aux = lbs.write_plists(root, "r1", ports, "http")
    data = plistlib.loads(primary.read_bytes())
    lb.check_sandbox_plist(data, root, "com.agent-lb.drill.sbx-r1", 2471)


def test_standby_start_refuses_a_bootstrap_that_is_a_link(tmp_path: Path) -> None:
    """The plist's program path is right but <root>/bin/lb-sandbox is a link to something else (a live
    launcher): reading the plist for the standby refuses, so nothing is spawned."""
    lb = _load()
    root = (tmp_path / ".agent-lb" / "sandboxes" / "r1").resolve()
    (root / "launchd").mkdir(parents=True)
    (root / "launchd" / "com.agent-lb.drill.sbx-r1.plist").write_bytes(plistlib.dumps(_plist(root)))
    (root / "bin").mkdir()
    bootstrap = root / "bin" / "lb-sandbox"
    bootstrap.write_text("#!/usr/bin/env python3\n")
    live = tmp_path / ".agent-lb" / "runtime" / "agent-lb" / "agent-lb"
    live.parent.mkdir(parents=True)
    live.write_text("#!/bin/sh\n")
    lb.__dict__.update(lb.sandbox_bindings(_config(tmp_path), home=tmp_path))
    assert lb.read_plist()["Label"] == "com.agent-lb.drill.sbx-r1"  # control: its own copy is accepted
    bootstrap.unlink()
    bootstrap.symlink_to(live)
    with pytest.raises(lb.SandboxRefused):
        lb.read_plist()
    bootstrap.unlink()
    os.link(live, bootstrap)
    with pytest.raises(lb.SandboxRefused):
        lb.read_plist()
