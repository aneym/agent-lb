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
    root.parent.chmod(0o700)  # and the sandboxes dir, whatever the umask
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


def _bootstrap(home: Path) -> Path:
    """A stand-in for the lb-sandbox installed beside lb-restart (never run here): a private single-link file
    outside the sandboxes. Tests point lb.sandbox_bootstrap at it; a checkout may be group-writable."""
    path = home / "installed-bin" / "lb-sandbox"
    if not os.path.lexists(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/usr/bin/env python3\n# stand-in for the installed lb-sandbox; never run\n")
        path.chmod(0o755)
    return path


def _use_bootstrap(lb: ModuleType, home: Path) -> Path:
    path = _bootstrap(home)
    lb.sandbox_bootstrap = lambda: path
    return path


def _plist(root: Path, **overrides) -> dict:
    home = root.parent.parent.parent
    data = {
        "Label": "com.agent-lb.drill.sbx-r1",
        "ProgramArguments": [
            str(_venv_python(home)),
            "-I",
            str(_bootstrap(home)),
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
        # S2-3 (M1): code loaded into the interpreter before any guard: a library, a startup hook, a module path.
        {"env": {"DYLD_INSERT_LIBRARIES": "lib/hook.dylib"}},
        {"env": {"PYTHONSTARTUP": "runtime/hook.py"}},
        {"env": {"PYTHONPATH": "runtime/elsewhere"}},
        {"env": {"PATH": "bin:/usr/bin:/bin"}},
    ],
)
def test_sandbox_plist_must_run_serve_against_its_own_store(tmp_path: Path, overrides: dict) -> None:
    """Finding: an in-root plist with a live DB URL or key path was trusted, so the standby booted on live."""
    lb = _load()
    root = (tmp_path / ".agent-lb" / "sandboxes" / "r1").resolve()
    _use_bootstrap(lb, root.parent.parent.parent)
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
    root.parent.chmod(0o700)  # lb-sandbox start makes the sandboxes dir 0700, whatever the umask
    ports = {"front": 2470, "primary": 2471, "standby": 2472, "gate": 2473, "edge_anthropic": 2474, "edge_openai": 2475}
    primary, _aux = lbs.write_plists(root, "r1", ports, "http")
    data = plistlib.loads(primary.read_bytes())
    lb.check_sandbox_plist(data, root, "com.agent-lb.drill.sbx-r1", 2471)


@pytest.mark.parametrize("plant", ["in-root-copy", "installed-link", "installed-hard-link", "installed-group-writable"])
def test_the_standby_runs_only_the_installed_lb_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plant: str
) -> None:
    """M2 (S2-3, lb-restart:1007,256): the standby ran <root>/bin/lb-sandbox, checked for shape (one link, a
    regular file) but not identity, so a regular Python file put there wrote live state before any confinement.
    The program is now the lb-sandbox installed beside lb-restart, outside every root; a plist naming the root's
    copy is refused, and the installed file must be a private single-link regular file. Popen is the OS edge."""
    lb = _load()
    config = _built_sandbox(tmp_path)
    root = config.parent
    _apply(lb, config, tmp_path)
    started: list[list[str]] = []

    class _Proc:
        pid = 424242

    monkeypatch.setattr(lb.subprocess, "Popen", lambda args, **kwargs: started.append(list(args)) or _Proc())
    lb.start_standby(1)  # control: the installed lb-sandbox, run with -I
    assert started and started[0][1:4] == ["-I", str(_bootstrap(tmp_path)), "_serve"]
    started.clear()
    installed = _bootstrap(tmp_path)
    if plant == "in-root-copy":
        # A regular single-link Python file in the root that would write live state (text only, never run).
        (root / "bin").mkdir()
        (root / "bin" / "lb-sandbox").write_text("open('/h/.agent-lb/state/front-preferred-port', 'w')\n")
        plist = _plist(root)
        plist["ProgramArguments"][2] = str(root / "bin" / "lb-sandbox")
        Path(json.loads(config.read_text())["plist"]).write_bytes(plistlib.dumps(plist))
    elif plant == "installed-link":
        real = installed.with_name("lb-sandbox.real")
        installed.rename(real)
        installed.symlink_to(real)
    elif plant == "installed-hard-link":
        os.link(installed, tmp_path / "second-name")
    else:
        installed.chmod(0o775)
    with pytest.raises(lb.SandboxRefused):
        lb.start_standby(1)
    assert started == []


def test_the_restart_guard_refuses_a_bootstrap_inside_the_sandboxes_dir(tmp_path: Path) -> None:
    """M2: even named as the installed copy, a bootstrap that sits inside the sandboxes dir is the root's to
    rewrite and is refused."""
    lb = _load()
    root = (tmp_path / ".agent-lb" / "sandboxes" / "r1").resolve()
    (root / "bin").mkdir(parents=True)
    inside = root / "bin" / "lb-sandbox"
    inside.write_text("#!/usr/bin/env python3\n")
    inside.chmod(0o755)
    lb.sandbox_bootstrap = lambda: inside
    with pytest.raises(lb.SandboxRefused, match="inside the sandboxes dir"):
        lb.check_sandbox_bootstrap(root)
    _use_bootstrap(lb, tmp_path)
    lb.check_sandbox_bootstrap(root)  # control


def test_a_startup_hook_planted_on_the_plist_pythonpath_never_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M1 (S2-3, lb-restart:1013, lb-sandbox:1555): the plist's PYTHONPATH names <root>/runtime, so a planted
    <root>/runtime/sitecustomize.py ran at interpreter start, before _serve or _boot reached a guard. Integration
    with the real interpreter: the argv and env start_standby builds are run with the stand-in interpreter
    swapped for this one and the bootstrap for a script that only reports it ran. The hook must not run; the
    control (the same argv without -I) shows the plant would have fired."""
    lb = _load()
    config = _built_sandbox(tmp_path)
    root = config.parent
    _apply(lb, config, tmp_path)
    built: list[tuple[list[str], dict]] = []

    class _Proc:
        pid = 424242

    def record(args, **kwargs):
        built.append((list(args), dict(kwargs["env"])))
        return _Proc()

    monkeypatch.setattr(lb.subprocess, "Popen", record)
    lb.start_standby(1)
    monkeypatch.undo()  # the real Popen again, for the real interpreter below
    args, env = built[0]
    assert env["PYTHONPATH"] == str(root / "runtime") and args[1] == "-I"
    hook_ran, script_ran = tmp_path / "hook-ran", tmp_path / "script-ran"
    (root / "runtime" / "sitecustomize.py").write_text(f"open({str(hook_ran)!r}, 'w').close()\n")
    script = tmp_path / "reports.py"
    script.write_text(f"open({str(script_ran)!r}, 'w').close()\n")

    def run(argv: list[str]) -> None:
        subprocess.run(argv, env=env, cwd=root, timeout=30, check=True, start_new_session=True)

    run([sys.executable, args[1], str(script), *args[3:]])
    assert script_ran.exists() and not hook_ran.exists(), "a planted sitecustomize.py ran before any guard"
    run([sys.executable, str(script), *args[3:]])  # control: without -I the plant fires
    assert hook_ran.exists()


# ---------------------------------------------------------------- S2 fix round 3 (review FAIL on ea866e22)


def _venv_python(home: Path) -> Path:
    """Where the installed runtime's venv python sits for this home (what every sandbox job must run)."""
    return home / ".agent-lb" / "runtime" / "agent-lb" / ".venv" / "bin" / "python"


def _native_head() -> bytes:
    """The first bytes of the interpreter running this test: what a native executable starts with here."""
    with open(os.path.realpath(sys.executable), "rb") as fh:
        return fh.read(4)


def _install_venv_python(home: Path) -> Path:
    """A stand-in for the installed venv's interpreter (never run here): bin/python -> a private file outside the
    sandboxes that starts like a native executable, in the dir the venv's pyvenv.cfg names as its home, as the
    real venv's python leads to its base interpreter."""
    python = _venv_python(home)
    python.parent.mkdir(parents=True, exist_ok=True)
    base = home / "base-python" / "python3"
    base.parent.mkdir(exist_ok=True)
    base.parent.chmod(0o755)
    base.write_bytes(_native_head() + bytes(60))
    base.chmod(0o755)
    (python.parent.parent / "pyvenv.cfg").write_text(f"home = {base.parent}\nversion_info = 3.14\n")
    (python.parent.parent / "pyvenv.cfg").chmod(0o644)  # what a venv writes, whatever the umask
    python.symlink_to(base)
    return python


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
    root.parent.chmod(0o700)  # and the sandboxes dir 0700, whatever the umask
    (root / "runtime").mkdir()
    Path(config["plist"]).write_bytes(plistlib.dumps(_plist(root)))
    if not os.path.lexists(_venv_python(tmp_path)):
        _install_venv_python(tmp_path)
    path = root / "lb-restart.json"
    path.write_text(json.dumps(config))
    return path


def _apply(lb: ModuleType, config: Path, home: Path) -> None:
    """apply_sandbox with the test's home in place of the real one (the guard reads it from pwd)."""
    lb._real_home = lambda: home
    _use_bootstrap(lb, home)
    lb.apply_sandbox(config)


def test_a_sandboxes_dir_swapped_for_a_link_after_the_guard_never_reaches_live_state(tmp_path: Path) -> None:
    """Finding (lb-restart:333): a valid sandbox named 'runtime'; after validation the sandboxes dir becomes a
    link to ~/.agent-lb, so <root>/lb-restart.lock names the live runtime/lb-restart.lock, which lock
    acquisition truncated. Every write now goes through the pinned root, and exec-by-path is refused."""
    lb = _load()
    config = _built_sandbox(tmp_path, "runtime")
    live = tmp_path / ".agent-lb" / "runtime"  # the live runtime dir the swapped path would name
    live.mkdir(exist_ok=True)
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
    live_lock.parent.mkdir(exist_ok=True)
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


# ---------------------------------------------------------------- S2-2 fix round 4 (live-safety review, cf915e10)

# What a shim on the interpreter path would run: a kickstart of the live service (text only, never executed).
SHIM_BODY = "#!/bin/sh\nlaunch" + "ctl kick" + "start -k gui/$UID/com.aneyman.agent-lb\n"


@pytest.mark.parametrize(
    "shim",
    ["link-into-root", "bin-dir-link", "group-writable", "in-root-name", "private-script", "native-elsewhere"],
)
def test_a_shim_on_the_interpreter_path_is_refused_and_never_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shim: str
) -> None:
    """M1 (lb-restart:285,969): the guard checked only the text <root>/runtime/.venv/bin/python, so a shim there
    (one that kickstarts the live agent-lb) passed and Popen ran it. The program is now the installed venv's
    python and what it leads to is checked just before the exec. subprocess.Popen is the OS edge, recorded.

    S2-3 (M3, lb-restart:294): a private same-user mode-0755 shell script outside the sandboxes passed every
    shape check. The interpreter is now checked for identity: a native executable in the dir the venv's
    pyvenv.cfg names, so a script (private-script) or a native file anywhere else (native-elsewhere) is refused."""
    lb = _load()
    config = _built_sandbox(tmp_path)
    root = config.parent
    _apply(lb, config, tmp_path)
    started: list[list[str]] = []

    class _Proc:
        pid = 424242

    def record(args, **kwargs):
        started.append(list(args))
        return _Proc()

    monkeypatch.setattr(lb.subprocess, "Popen", record)
    lb.start_standby(1)  # control: the installed venv's python is accepted
    assert [args[0] for args in started] == [str(_venv_python(tmp_path))]
    started.clear()
    shim_file = root / "shim"
    shim_file.write_text(SHIM_BODY)
    shim_file.chmod(0o755)
    python = _venv_python(tmp_path)
    if shim == "link-into-root":
        python.unlink()
        python.symlink_to(shim_file)
    elif shim == "bin-dir-link":
        python.parent.rename(python.parent.with_name("bin.real"))
        (root / "fakebin").mkdir()
        (root / "fakebin" / "python").symlink_to(shim_file)
        python.parent.symlink_to(root / "fakebin", target_is_directory=True)
    elif shim == "group-writable":
        writable = tmp_path / "writable-python"
        writable.write_text("#!/bin/sh\n")
        writable.chmod(0o775)
        python.unlink()
        python.symlink_to(writable)
    elif shim == "private-script":
        private = tmp_path / "private" / "python3"
        private.parent.mkdir(mode=0o700)
        private.write_text(SHIM_BODY)
        private.chmod(0o755)
        python.unlink()
        python.symlink_to(private)
    elif shim == "native-elsewhere":
        elsewhere = tmp_path / "elsewhere" / "python3"
        elsewhere.parent.mkdir(mode=0o755)
        elsewhere.write_bytes((tmp_path / "base-python" / "python3").read_bytes())
        elsewhere.chmod(0o755)
        python.unlink()
        python.symlink_to(elsewhere)
    else:
        in_root = root / "runtime" / ".venv" / "bin" / "python"
        in_root.parent.mkdir(parents=True)
        in_root.symlink_to(shim_file)
        plist = _plist(root)
        plist["ProgramArguments"][0] = str(in_root)
        Path(json.loads(config.read_text())["plist"]).write_bytes(plistlib.dumps(plist))
    with pytest.raises(lb.SandboxRefused):
        lb.start_standby(1)
    assert started == []


def test_a_kickstart_never_runs_a_loaded_job_on_another_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M1, launchd's side: a kickstart execs the job launchd loaded, not the plist file. The loaded job's program
    and arguments (from the job manager's print, the OS edge, faked here) must be the checked interpreter on
    `lb-sandbox _serve <root>`, else nothing is kickstarted."""
    lb = _load()
    config = _built_sandbox(tmp_path)
    root = config.parent
    _apply(lb, config, tmp_path)
    python = str(_venv_python(tmp_path))
    plist = plistlib.loads(Path(json.loads(config.read_text())["plist"]).read_bytes())
    shown = {"program": python, "arguments": list(plist["ProgramArguments"])}
    kicked: list[list[str]] = []

    def job_print(argv, **kwargs):
        text = _job_print(lb.LABEL, plist, program=shown["program"], arguments=shown["arguments"])
        return subprocess.CompletedProcess(argv, 0, stdout=text, stderr="")

    monkeypatch.setattr(lb.subprocess, "run", job_print)
    monkeypatch.setattr(lb.subprocess, "Popen", lambda argv, **kwargs: kicked.append(list(argv)))
    lb.kickstart_primary(wait=False)  # control
    assert len(kicked) == 1 and kicked[0][-1] == f"gui/{os.getuid()}/{lb.LABEL}"
    kicked.clear()
    in_root = str(root / "runtime" / ".venv" / "bin" / "python")
    for program, arguments in (
        (in_root, [in_root, *shown["arguments"][1:]]),
        (python, [python, "/h/.agent-lb/runtime/agent-lb/.venv/bin/agent-lb", "--port", "2471"]),
        # S2-3: a job loaded without -I (a planted sitecustomize.py runs) or on the root's own copy (M2).
        (python, [python, str(_bootstrap(tmp_path)), "_serve", str(root)]),
        (python, [python, "-I", str(root / "bin" / "lb-sandbox"), "_serve", str(root)]),
    ):
        shown.update(program=program, arguments=arguments)
        with pytest.raises(lb.SandboxRefused):
            lb.kickstart_primary(wait=False)
    assert kicked == []


def test_front_state_replaced_between_open_and_fstat_is_read_not_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (09a842e65..cf915e10, lb-restart:564): the front replaces front.json atomically
    (agent-lb-front.mjs:191). Replaced after our open, the inode we hold has st_nlink 0, which read as "not a
    single link" and made a legitimate sandbox restart exit 2. A second hard link is still refused."""
    lb = _load()
    config = _built_sandbox(tmp_path)
    root = config.parent
    _apply(lb, config, tmp_path)
    state = root / "state"
    state.mkdir(mode=0o700)
    (state / "front.json").write_text('{"preferred": 2471}')
    real_open = os.open
    replaced: list[bool] = []

    def open_then_front_replaces(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if path == "front.json" and not replaced:
            replaced.append(True)  # what the front does: write front.json.tmp, rename over front.json
            (state / "front.json.tmp").write_text('{"preferred": 2472}')
            os.replace(state / "front.json.tmp", state / "front.json")
        return fd

    monkeypatch.setattr(os, "open", open_then_front_replaces)
    got = lb.read_state(lb.FRONT_STATE)
    monkeypatch.setattr(os, "open", real_open)
    assert replaced and got is not None and got[0] == '{"preferred": 2472}'
    os.link(state / "front.json", tmp_path / "second-name")
    with pytest.raises(lb.SandboxRefused, match="single-link"):
        lb.read_state(lb.FRONT_STATE)


# ---------------------------------------------------------------- lbsb-4 fix round (S2-3 review, parked findings)


def _job_print(label: str, plist: dict, program: str | None = None, arguments: list[str] | None = None) -> str:
    """`launchctl print gui/<uid>/<label>` for a job loaded from plist, in the layout launchd prints on macOS 26
    (taken from a real print of a throwaway com.agent-lb.drill.* job): program and arguments, working directory,
    stream paths, the three environment blocks, then launchd's own fields."""
    argv = arguments if arguments is not None else list(plist["ProgramArguments"])
    lines = [f"gui/{os.getuid()}/{label} = {{", "\tactive count = 0", "\tpath = /x/job.plist", "\ttype = LaunchAgent"]
    lines += ["\tstate = not running", "", f"\tprogram = {program or argv[0]}", "\targuments = {"]
    lines += [f"\t\t{a}" for a in argv] + ["\t}", ""]
    if "WorkingDirectory" in plist:
        lines += [f"\tworking directory = {plist['WorkingDirectory']}", ""]
    for key, shown in (("StandardOutPath", "stdout path"), ("StandardErrorPath", "stderr path")):
        if key in plist:
            lines.append(f"\t{shown} = {plist[key]}")
    lines += ["\tinherited environment = {", "\t\tSSH_AUTH_SOCK => /private/tmp/launchd/Listeners", "\t}", ""]
    lines += ["\tdefault environment = {", "\t\tPATH => /usr/bin:/bin:/usr/sbin:/sbin", "\t}", ""]
    env = {"OSLogRateLimit": "64", **plist.get("EnvironmentVariables", {}), "XPC_SERVICE_NAME": label}
    lines += ["\tenvironment = {", *(f"\t\t{k} => {v}" for k, v in env.items()), "\t}", ""]
    lines += ["\tdomain = gui/501 [100023]", "\truns = 0", "\tresource coalition = {", "\t\tID = 1", "\t}"]
    lines += ["", "\tproperties = inferred program", "}"]
    return "\n".join(lines) + "\n"


@pytest.mark.skipif(sys.platform != "darwin", reason="launchd is macOS")
@pytest.mark.parametrize(
    "hostile",
    [
        {"env": {"DYLD_INSERT_LIBRARIES": "lib/hook.dylib"}},
        {"StandardErrorPath": "LIVE"},
        {"StandardOutPath": "LIVE"},
        {"ProgramArguments": "+--evil"},
        {"env": {"AGENT_LB_FEDERATION_PEER_URL": "http://127.0.0.1:2599"}},
        {"WorkingDirectory": "/tmp"},
    ],
)
def test_a_kickstart_never_runs_a_loaded_job_other_than_its_checked_plist(tmp_path: Path, hostile: dict) -> None:
    """S2-3 review (lb-restart:1242): the loaded-job guard checked only the program and the argv prefix. A job
    loaded from a hostile plist with the expected argv plus DYLD_INSERT_LIBRARIES, or with a stderr path into
    live state, and the plist on disk restored afterwards, passed and was kickstarted. The loaded job's whole
    argv, effective environment, working directory and stream paths must be the checked plist's.

    Integration with the real launchd: each job is bootstrapped for real (never started: no RunAtLoad, no
    KeepAlive, and the program is a stand-in), the guard reads it with the real `launchctl print`, and the job is
    booted out in all cases."""
    lb = _load()
    config = _built_sandbox(tmp_path)
    label = f"com.agent-lb.drill.sbx-unit{os.getpid()}"
    data = json.loads(config.read_text())
    data["label"] = label
    config.write_text(json.dumps(data))
    disk = Path(data["plist"])
    clean = {**plistlib.loads(disk.read_bytes()), "Label": label}
    disk.write_bytes(plistlib.dumps(clean))  # what stays on disk: the checked plist
    _apply(lb, config, tmp_path)
    live = tmp_path / ".agent-lb" / "state" / "front.json"
    loaded = json.loads(json.dumps(clean))
    for key, value in hostile.items():
        if key == "env":
            loaded["EnvironmentVariables"].update(value)
        elif value == "LIVE":
            loaded[key] = str(live)
        elif key == "ProgramArguments":
            loaded[key] = [*loaded[key], value[1:]]
        else:
            loaded[key] = value
    job = tmp_path / "loaded.plist"
    target = f"gui/{os.getuid()}/{label}"

    def load(body: dict) -> None:
        job.write_bytes(plistlib.dumps(body))
        subprocess.run(["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(job)], check=True, timeout=30)

    def unload() -> None:
        subprocess.run(["/bin/launchctl", "bootout", target], capture_output=True, timeout=30, check=False)

    try:
        load(clean)
        lb.check_loaded_sandbox_job()  # control: the job loaded from the checked plist passes
        unload()
        load(loaded)
        with pytest.raises(lb.SandboxRefused, match="loaded job"):
            lb.check_loaded_sandbox_job()
    finally:
        unload()
    assert subprocess.run(["/bin/launchctl", "print", target], capture_output=True, timeout=30).returncode != 0


@pytest.mark.parametrize(
    "forged",
    [
        "\n\t}\n\tforged = {\n\t\tDYLD_INSERT_LIBRARIES => /x/hook.dylib",  # hides an entry in a block of its own
        "\n\tstderr path = /x/live.log",  # a stream path of its own
        "\n\t\tDYLD_INSERT_LIBRARIES => /x/hook.dylib",  # an entry past the end of the block
    ],
)
def test_a_value_with_a_line_break_cannot_pass_for_launchd_structure(tmp_path: Path, forged: str) -> None:
    """launchctl print shows values raw, so a loaded value with a line break in it could make an entry read as
    outside the environment block (unchecked). Such a print is refused, and a plist value with a control
    character is refused before anything is loaded. Golden-style: the print layout is launchd's (see _job_print)."""
    lb = _load()
    config = _built_sandbox(tmp_path)
    _apply(lb, config, tmp_path)
    plist = plistlib.loads(Path(json.loads(config.read_text())["plist"]).read_bytes())
    python = _venv_python(tmp_path)
    want = list(plist["ProgramArguments"])
    lb.check_loaded_job_print(_job_print(lb.LABEL, plist), plist, python, want)  # control
    text = _job_print(lb.LABEL, plist).replace(
        "\t\tPYTHONPATH => ", "\t\tAGENT_LB_X => x" + forged + "\n\t\tPYTHONPATH => "
    )
    with pytest.raises(lb.SandboxRefused):
        lb.check_loaded_job_print(text, plist, python, want)
    bad = json.loads(json.dumps(plist))
    bad["EnvironmentVariables"]["AGENT_LB_X"] = "x" + forged
    with pytest.raises(lb.SandboxRefused, match="control character"):
        lb.check_sandbox_plist(bad, config.parent, lb.LABEL, lb.PRIMARY_PORT)
