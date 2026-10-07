"""lb-restart --sandbox: the guard and the constant rebinding.

Unit level on purpose: the guard is a decision table with many edge cases, and
a CLI test that got past a broken guard could restart the live agent-lb. The
real restart of a sandbox is proven by scripts/lb-sandbox-check on Studio.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
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
    for root in (tmp_path / ".agent-lb" / "sandboxes", tmp_path / ".agent-lb" / "runtime"):
        with pytest.raises(lb.SandboxRefused):
            lb.sandbox_bindings(_config(tmp_path, root=str(root)), home=tmp_path)
    config = _config(tmp_path)
    del config["standby_port"]
    with pytest.raises(lb.SandboxRefused):
        lb.sandbox_bindings(config, home=tmp_path)


@pytest.mark.parametrize("planted", ["lb-restart.lock", "logs/sync.log", "state/front-preferred-port", "backups"])
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
