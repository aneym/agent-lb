"""lb-restart --sandbox: the guard and the constant rebinding.

Unit level on purpose: the guard is a decision table with many edge cases, and
a CLI test that got past a broken guard could restart the live agent-lb. The
real restart of a sandbox is proven by scripts/lb-sandbox-check on Studio.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
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
    root.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)  # as lb-sandbox start makes it; the pinned root must be 0700 and ours
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


def test_state_writes_never_follow_a_state_dir_swapped_for_a_link(tmp_path: Path) -> None:
    """Finding: with state/ swapped for a link to live state after the guard, the absolute-path rename replaced
    the live front-preferred-port and the pidfile cleanup unlinked the live lb-standby.pid. Both now go through
    a descriptor for the real state dir under the root, and refuse a link."""
    lb = _load()
    root = tmp_path / ".agent-lb" / "sandboxes" / "r1"
    (root / "state").mkdir(parents=True)
    live = tmp_path / ".agent-lb" / "state"
    live.mkdir(parents=True)
    files = {"front-preferred-port": "2457\n", "front-preferred-port.tmp": "2459\n", "lb-standby.pid": "4242\n"}
    for name, text in files.items():
        (live / name).write_text(text)
    lb.__dict__.update(lb.sandbox_bindings(_config(tmp_path), home=tmp_path))
    (root / "state").rmdir()
    (root / "state").symlink_to(live, target_is_directory=True)
    with pytest.raises(lb.SandboxRefused):
        lb.remove_state(lb.STANDBY_PIDFILE)
    with pytest.raises(lb.SandboxRefused):
        lb.set_preferred(2472)
    assert {name: (live / name).read_text() for name in files} == files


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
    root.chmod(0o700)
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


# ---------------------------------------------------------------- S2 fix round 3 (review FAIL on ea866e22)


def _built_sandbox(tmp_path: Path, name: str = "r1") -> Path:
    """A sandbox laid out as lb-sandbox start leaves it (config, plist, bootstrap); returns the config path."""
    root = tmp_path / ".agent-lb" / "sandboxes" / name
    config = _config(
        tmp_path,
        root=str(root),
        plist=str(root / "launchd" / "com.agent-lb.drill.sbx-r1.plist"),
        runtime=str(root / "runtime"),
        state_dir=str(root / "state"),
    )
    (root / "launchd").mkdir(parents=True)
    root.chmod(0o700)  # lb-sandbox start makes the root 0700; the pinned root must be
    (root / "runtime").mkdir()
    (root / "bin").mkdir()
    (root / "bin" / "lb-sandbox").write_text("#!/usr/bin/env python3\n")
    Path(config["plist"]).write_bytes(plistlib.dumps(_plist(root)))
    path = root / "lb-restart.json"
    path.write_text(json.dumps(config))
    return path


def _apply(lb: ModuleType, config: Path, home: Path) -> None:
    """apply_sandbox with the test's home in place of the real one (the guard reads it from pwd)."""
    lb._real_home = lambda: home
    lb.apply_sandbox(config)


def test_a_sandboxes_dir_swapped_for_a_link_after_the_guard_never_reaches_live_state(tmp_path: Path) -> None:
    """Finding (lb-restart:333): a valid sandbox named 'runtime'; after validation the sandboxes dir becomes a
    link to ~/.agent-lb, so <root>/lb-restart.lock names the live runtime/lb-restart.lock, which lock
    acquisition truncated. Every write now goes through the pinned root, and exec-by-path is refused."""
    lb = _load()
    config = _built_sandbox(tmp_path, "runtime")
    live = tmp_path / ".agent-lb" / "runtime"  # the live runtime dir the swapped path would name
    live.mkdir()
    for name, text in (("lb-restart.lock", "pid=1 live\n"), ("sync.log", "live log\n")):
        (live / name).write_text(text)
    (tmp_path / ".agent-lb" / "state").mkdir()
    _apply(lb, config, tmp_path)
    sandboxes = tmp_path / ".agent-lb" / "sandboxes"
    sandboxes.rename(tmp_path / ".agent-lb" / "moved")
    sandboxes.symlink_to(tmp_path / ".agent-lb")
    # Every state operation re-checks that the root path still names the pinned root (S2-2): the run stops.
    with pytest.raises(lb.SandboxRefused):
        with lb.Lock("unit", 1):
            pass
    with pytest.raises(lb.SandboxRefused):
        lb.sync_log("unit")
    assert (live / "lb-restart.lock").read_text() == "pid=1 live\n"
    assert (live / "sync.log").read_text() == "live log\n"
    assert not (tmp_path / ".agent-lb" / "moved" / "runtime" / "logs").exists()
    with pytest.raises(lb.SandboxRefused):
        lb.verify_sandbox_ancestry()  # what start_standby runs before it execs anything by path


def test_a_sandboxes_dir_that_is_already_a_link_is_refused(tmp_path: Path) -> None:
    """The same swap before the guard: resolving made ~/.agent-lb/runtime look directly under the sandboxes."""
    lb = _load()
    _built_sandbox(tmp_path, "runtime")
    sandboxes = tmp_path / ".agent-lb" / "sandboxes"
    sandboxes.rename(tmp_path / ".agent-lb" / "moved")
    sandboxes.symlink_to(tmp_path / ".agent-lb")
    live_lock = tmp_path / ".agent-lb" / "runtime" / "lb-restart.lock"
    live_lock.parent.mkdir()
    live_lock.write_text("pid=1 live\n")
    with pytest.raises(lb.SandboxRefused, match="link"):
        _apply(lb, tmp_path / ".agent-lb" / "moved" / "runtime" / "lb-restart.json", tmp_path)
    assert live_lock.read_text() == "pid=1 live\n"
    assert lb.SANDBOX_ROOT is None and lb.LOCK_FILE == lb.LIVE_BINDINGS["LOCK_FILE"]


@pytest.mark.parametrize(
    "planted", ["lb-restart.lock", "state/front.json", "watchdog.pause", "launchd/com.agent-lb.drill.sbx-r1.plist"]
)
def test_an_in_root_symlink_is_refused_before_anything_is_written(tmp_path: Path, planted: str) -> None:
    """Finding (lb-restart:176): the guard resolved paths first, so lb-restart.lock -> lb-restart.json passed
    as an in-root single-link file and lock acquisition overwrote the config. The link itself is refused."""
    lb = _load()
    config = _built_sandbox(tmp_path)
    root = config.parent
    before = config.read_bytes()
    target = root / "launchd" / "copy.plist" if planted.endswith(".plist") else config
    if planted.endswith(".plist"):
        target.write_bytes((root / planted).read_bytes())
        (root / planted).unlink()
    (root / planted).parent.mkdir(parents=True, exist_ok=True)
    (root / planted).symlink_to(target)
    with pytest.raises(lb.SandboxRefused, match="link"):
        _apply(lb, config, tmp_path)
    assert config.read_bytes() == before


def test_an_in_root_symlink_planted_after_the_guard_is_refused_at_use(tmp_path: Path) -> None:
    lb = _load()
    config = _built_sandbox(tmp_path)
    before = config.read_bytes()
    _apply(lb, config, tmp_path)
    (config.parent / "lb-restart.lock").symlink_to(config)
    with pytest.raises(lb.SandboxRefused):
        with lb.Lock("unit", 1):
            pass
    (config.parent / "state").mkdir()
    (config.parent / "state" / "front.json").symlink_to(config)
    with pytest.raises(lb.SandboxRefused):
        lb.front_routes_to(2471, timeout=0)
    assert config.read_bytes() == before


@pytest.mark.parametrize(
    "argv",
    [
        ["--reap", "{pid}", "0", "--sandbox={config}"],
        ["--reap", "{pid}", "0", "--sandbox"],
        ["--reap", "{pid}", "0", "--sand", "{config}"],
        ["--reap", "{pid}", "0", "--sandbox", "{config}", "--extra"],
        ["--reap", "{pid}", "0", "{config}"],
        ["--reap={pid}", "0"],
        ["--reason", "x", "--reap", "{pid}", "0"],
    ],
)
def test_the_reaper_refuses_any_argument_list_it_did_not_write(tmp_path: Path, argv: list[str]) -> None:
    """Finding (lb-restart:874): '--reap <live standby pid> 0 --sandbox=<config>' skipped binding, kept the live
    standby port and SIGKILLed the live standby. A stand-in that looks like the live standby must survive."""
    standin = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", "agent-lb", "--port", "2459"],
        start_new_session=True,
    )
    try:
        words = [w.format(pid=standin.pid, config=tmp_path / "lb-restart.json") for w in argv]
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), *words],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env={**os.environ, "HOME": str(tmp_path)},  # a broken parser logs to this home, never the live one
        )
        assert proc.returncode == 2, proc.stderr
        assert standin.poll() is None, "the stand-in standby was signalled"
    finally:
        standin.kill()
        standin.wait(10)


def test_the_reaper_reads_back_exactly_what_spawn_reaper_writes() -> None:
    lb = _load()
    assert lb.parse_reaper_args(["--reap", "123", "90"]) == (123, 90.0, None)
    assert lb.parse_reaper_args(["--reap", "123", "90", "--sandbox", "/x/c.json"]) == (123, 90.0, Path("/x/c.json"))
    for bad in (["--reap", "0", "90"], ["--reap", "-5", "90"], ["--reap", "1", "90"], ["--reap", "12", "nan"]):
        with pytest.raises(lb.SandboxRefused):
            lb.parse_reaper_args(bad)


# ---------------------------------------------------------------- S2-2 fix round (M5, Codex live-safety review of S1)


def _live_state(tmp_path: Path) -> Path:
    """A stand-in for the live ~/.agent-lb/state: a preference, a leftover preference temp file and a pidfile."""
    live = tmp_path / ".agent-lb" / "state"
    live.mkdir(parents=True)
    (live / "front-preferred-port").write_text("2457\n")
    (live / "front-preferred-port.tmp").write_text("2459\n")
    (live / "lb-standby.pid").write_text("4242\n")
    return live


def test_a_state_dir_swapped_between_write_and_rename_never_replaces_the_live_preference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M5 (lb-restart:484): after the temp file is written, state/ becomes a link to the live state dir. A rename
    by path then moved the live front-preferred-port.tmp over the live preference. The rename now goes through
    the state dir's descriptor (the real sandbox dir), and the swap is refused."""
    lb = _load()
    config = _built_sandbox(tmp_path)
    root = config.parent
    live = _live_state(tmp_path)
    _apply(lb, config, tmp_path)
    (root / "state").mkdir(mode=0o700)
    real_replace = os.replace

    def swap_then_replace(*args, **kwargs):
        if not (root / "state").is_symlink():
            (root / "state").rename(root / "state.moved")
            (root / "state").symlink_to(live)
        return real_replace(*args, **kwargs)

    monkeypatch.setattr(os, "replace", swap_then_replace)
    with pytest.raises(lb.SandboxRefused, match="swapped|link"):
        lb.set_preferred(2472)
    monkeypatch.setattr(os, "replace", real_replace)
    assert (live / "front-preferred-port").read_text() == "2457\n"
    assert (live / "front-preferred-port.tmp").read_text() == "2459\n"
    assert (root / "state.moved" / "front-preferred-port").read_text() == "2472\n"  # renamed in the sandbox's own dir


def test_a_state_dir_swapped_before_pidfile_cleanup_never_unlinks_the_live_pidfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M5 (lb-restart:1028): state/ swapped for a link to the live state dir before the standby pidfile is
    cleaned up. An unlink by path removed the live lb-standby.pid; now the walk refuses the link."""
    lb = _load()
    config = _built_sandbox(tmp_path)
    root = config.parent
    live = _live_state(tmp_path)
    _apply(lb, config, tmp_path)
    monkeypatch.setattr(lb, "listener_pid", lambda port: None)  # no standby left: the cleanup-only branch
    (root / "state").symlink_to(live)
    with pytest.raises(lb.SandboxRefused):
        lb.stop_leftover_standby(0)
    assert (live / "lb-standby.pid").read_text() == "4242\n"
    with pytest.raises(lb.SandboxRefused):
        lb.front_routes_to(2471, timeout=0)  # a read through the swapped dir is refused too


def test_a_hard_linked_pidfile_is_refused_not_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lb = _load()
    config = _built_sandbox(tmp_path)
    root = config.parent
    live = _live_state(tmp_path)
    _apply(lb, config, tmp_path)
    monkeypatch.setattr(lb, "listener_pid", lambda port: None)
    (root / "state").mkdir(mode=0o700)
    os.link(live / "lb-standby.pid", root / "state" / "lb-standby.pid")
    with pytest.raises(lb.SandboxRefused, match="single-link"):
        lb.stop_leftover_standby(0)
    assert (live / "lb-standby.pid").read_text() == "4242\n"
    assert (root / "state" / "lb-standby.pid").exists()
    (root / "state" / "lb-standby.pid").unlink()
    (root / "state" / "lb-standby.pid").write_text("1\n")
    lb.stop_leftover_standby(0)  # control: its own single-link pidfile is removed
    assert not (root / "state" / "lb-standby.pid").exists()


@pytest.mark.parametrize("which,mode", [("root", 0o755), ("root", 0o710), ("sandboxes", 0o777)])
def test_a_root_lb_sandbox_did_not_make_is_refused(tmp_path: Path, which: str, mode: int) -> None:
    """p13D: the root must be one lb-sandbox made (0700, ours) in a sandboxes dir no one else can write."""
    lb = _load()
    config = _built_sandbox(tmp_path)
    before = config.read_bytes()
    target = config.parent if which == "root" else config.parent.parent
    target.chmod(mode)
    try:
        with pytest.raises(lb.SandboxRefused, match="mode"):
            _apply(lb, config, tmp_path)
    finally:
        target.chmod(0o700)
    assert lb.SANDBOX_ROOT is None and lb.LOCK_FILE == lb.LIVE_BINDINGS["LOCK_FILE"]
    assert not (config.parent / "lb-restart.lock").exists() and config.read_bytes() == before
    _apply(lb, config, tmp_path)  # control: the same root at 0700 is accepted
    with lb.Lock("unit", 1):
        pass
