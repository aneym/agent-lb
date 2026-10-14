

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
        assert lb.owned_by_sandbox(other.pid) is True
        lb.__dict__.update(lb.sandbox_bindings(_config(tmp_path), home=tmp_path))
        assert lb.owned_by_sandbox(other.pid) is False
        assert lb.owned_by_sandbox(mine.pid) is True
    finally:
        for proc in (other, mine):
            proc.kill()
            proc.wait(10)





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

    lb = _load()
    root = tmp_path / ".agent-lb" / "sandboxes" / "r1"
    root.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    root.parent.chmod(0o700)
    live = tmp_path / ".agent-lb" / "state" / "front-preferred-port"
    live.parent.mkdir(parents=True)
    live.write_text("2457\n")
    lb.__dict__.update(lb.sandbox_bindings(_config(tmp_path), home=tmp_path))
    lb._root_on_own_volume = lambda root_info, sandboxes_info: True
    os.link(live, root / "lb-restart.lock")
    with pytest.raises(lb.SandboxRefused):
        with lb.Lock("unit", 1):
            pass
    (root / "state").mkdir()
    os.link(live, root / "state" / "front-preferred-port.tmp")
    with pytest.raises(lb.SandboxRefused):
        lb.set_preferred(2472)
    assert live.read_text() == "2457\n"
    os.unlink(root / "lb-restart.lock")
    with lb.Lock("unit", 1):
        pass


def test_state_writes_never_follow_a_state_dir_swapped_for_a_link(tmp_path: Path) -> None:

    lb = _load()
    root = tmp_path / ".agent-lb" / "sandboxes" / "r1"
    (root / "state").mkdir(parents=True)
    live = tmp_path / ".agent-lb" / "state"
    live.mkdir(parents=True)
    files = {"front-preferred-port": "2457\n", "front-preferred-port.tmp": "2459\n", "lb-standby.pid": "4242\n"}
    for name, text in files.items():
        (live / name).write_text(text)
    lb.__dict__.update(lb.sandbox_bindings(_config(tmp_path), home=tmp_path))
    lb._root_on_own_volume = lambda root_info, sandboxes_info: True
    (root / "state").rmdir()
    (root / "state").symlink_to(live, target_is_directory=True)
    with pytest.raises(lb.SandboxRefused):
        lb.remove_state(lb.STANDBY_PIDFILE)
    with pytest.raises(lb.SandboxRefused):
        lb.set_preferred(2472)
    assert {name: (live / name).read_text() for name in files} == files


def _bootstrap(home: Path) -> Path:

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

        {"env": {"AGENT_LB_FEDERATION_PEER_HOST": "127.0.0.1:2457"}},
        {"env": {"AGENT_LB_PORT": "2455"}},
        {"env": {"AGENT_LB_FEDERATION_PUSH_PATH": "../../../../state/federation-push.json"}},
        {"env": {"AGENT_LB_CONVERSATION_ARCHIVE_DIR": "~/.agent-lb/conversation-archive"}},
        {"Program": "/h/.agent-lb/runtime/agent-lb/.venv/bin/agent-lb"},
        {"StandardOutPath": "/h/.agent-lb/agent-lb.log"},

        {"env": {"DYLD_INSERT_LIBRARIES": "lib/hook.dylib"}},
        {"env": {"PYTHONSTARTUP": "runtime/hook.py"}},
        {"env": {"PYTHONPATH": "runtime/elsewhere"}},
        {"env": {"PATH": "bin:/usr/bin:/bin"}},
    ],
)
def test_sandbox_plist_must_run_serve_against_its_own_store(tmp_path: Path, overrides: dict) -> None:

    lb = _load()
    root = (tmp_path / ".agent-lb" / "sandboxes" / "r1").resolve()
    _use_bootstrap(lb, root.parent.parent.parent)
    lb.check_sandbox_plist(_plist(root), root, "com.agent-lb.drill.sbx-r1", 2471)
    with pytest.raises(lb.SandboxRefused):
        lb.check_sandbox_plist(_plist(root, **overrides), root, "com.agent-lb.drill.sbx-r1", 2471)





def _load_sandbox() -> ModuleType:
    path = SCRIPT.parent / "lb-sandbox"
    loader = importlib.machinery.SourceFileLoader("lb_sandbox_for_restart_parity", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_the_plist_lb_sandbox_writes_passes_the_restart_guard(tmp_path: Path) -> None:

    lb, lbs = _load(), _load_sandbox()
    root = (tmp_path / ".agent-lb" / "sandboxes" / "r1").resolve()
    root.mkdir(parents=True, mode=0o700)
    root.parent.chmod(0o700)
    ports = {"front": 2470, "primary": 2471, "standby": 2472, "gate": 2473, "edge_anthropic": 2474, "edge_openai": 2475}
    primary, _aux = lbs.write_plists(root, "r1", ports, "http")
    data = plistlib.loads(primary.read_bytes())
    lb.check_sandbox_plist(data, root, "com.agent-lb.drill.sbx-r1", 2471)


@pytest.mark.parametrize("plant", ["in-root-copy", "installed-link", "installed-hard-link", "installed-group-writable"])
def test_the_standby_runs_only_the_installed_lb_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plant: str
) -> None:

    lb = _load()
    config = _built_sandbox(tmp_path)
    root = config.parent
    _apply(lb, config, tmp_path)
    started: list[list[str]] = []

    class _Proc:
        pid = 424242

    monkeypatch.setattr(lb.subprocess, "Popen", lambda args, **kwargs: started.append(list(args)) or _Proc())
    lb.start_standby(1)
    assert started and started[0][1:4] == ["-I", str(_bootstrap(tmp_path)), "_serve"]
    started.clear()
    installed = _bootstrap(tmp_path)
    if plant == "in-root-copy":

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
    lb.check_sandbox_bootstrap(root)


def test_a_startup_hook_planted_on_the_plist_pythonpath_never_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

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
    monkeypatch.undo()
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
    run([sys.executable, str(script), *args[3:]])
    assert hook_ran.exists()





def _venv_python(home: Path) -> Path:

    return home / ".agent-lb" / "runtime" / "agent-lb" / ".venv" / "bin" / "python"


def _native_head() -> bytes:

    with open(os.path.realpath(sys.executable), "rb") as fh:
        return fh.read(4)


def _install_venv_python(home: Path) -> Path:

    python = _venv_python(home)
    python.parent.mkdir(parents=True, exist_ok=True)
    base = home / "base-python" / "python3"
    base.parent.mkdir(exist_ok=True)
    base.parent.chmod(0o755)
    base.write_bytes(_native_head() + bytes(60))
    base.chmod(0o755)
    (python.parent.parent / "pyvenv.cfg").write_text(f"home = {base.parent}\nversion_info = 3.14\n")
    (python.parent.parent / "pyvenv.cfg").chmod(0o644)
    python.symlink_to(base)
    return python


def _built_sandbox(tmp_path: Path, name: str = "r1") -> Path:

    root = tmp_path / ".agent-lb" / "sandboxes" / name
    config = _config(
        tmp_path,
        root=str(root),
        plist=str(root / "launchd" / "com.agent-lb.drill.sbx-r1.plist"),
        runtime=str(root / "runtime"),
        state_dir=str(root / "state"),
    )
    (root / "launchd").mkdir(parents=True)
    root.chmod(0o700)
    root.parent.chmod(0o700)
    (root / "runtime").mkdir()
    Path(config["plist"]).write_bytes(plistlib.dumps(_plist(root)))
    if not os.path.lexists(_venv_python(tmp_path)):
        _install_venv_python(tmp_path)
    path = root / "lb-restart.json"
    path.write_text(json.dumps(config))
    return path


def _apply(lb: ModuleType, config: Path, home: Path, own_volume: bool = True) -> None:

    lb._real_home = lambda: home
    _use_bootstrap(lb, home)
    if own_volume:
        lb._root_on_own_volume = lambda root_info, sandboxes_info: True
    lb.apply_sandbox(config)


def test_a_sandboxes_dir_swapped_for_a_link_after_the_guard_never_reaches_live_state(tmp_path: Path) -> None:

    lb = _load()
    config = _built_sandbox(tmp_path, "runtime")
    live = tmp_path / ".agent-lb" / "runtime"
    live.mkdir(exist_ok=True)
    for name, text in (("lb-restart.lock", "pid=1 live\n"), ("sync.log", "live log\n")):
        (live / name).write_text(text)
    (tmp_path / ".agent-lb" / "state").mkdir()
    _apply(lb, config, tmp_path)
    sandboxes = tmp_path / ".agent-lb" / "sandboxes"
    sandboxes.rename(tmp_path / ".agent-lb" / "moved")
    sandboxes.symlink_to(tmp_path / ".agent-lb")

    with pytest.raises(lb.SandboxRefused):
        with lb.Lock("unit", 1):
            pass
    with pytest.raises(lb.SandboxRefused):
        lb.sync_log("unit")
    assert (live / "lb-restart.lock").read_text() == "pid=1 live\n"
    assert (live / "sync.log").read_text() == "live log\n"
    assert not (tmp_path / ".agent-lb" / "moved" / "runtime" / "logs").exists()
    with pytest.raises(lb.SandboxRefused):
        lb.verify_sandbox_ancestry()


@pytest.mark.parametrize("via", ["moved", "sandboxes"])
def test_a_sandboxes_dir_that_is_already_a_link_is_refused(tmp_path: Path, via: str) -> None:

    lb = _load()
    _built_sandbox(tmp_path, "runtime")
    sandboxes = tmp_path / ".agent-lb" / "sandboxes"
    sandboxes.rename(tmp_path / ".agent-lb" / "moved")
    sandboxes.symlink_to(tmp_path / ".agent-lb")
    live_lock = tmp_path / ".agent-lb" / "runtime" / "lb-restart.lock"
    live_lock.parent.mkdir(exist_ok=True)
    live_lock.write_text("pid=1 live\n")
    with pytest.raises(lb.SandboxRefused, match="link" if via == "sandboxes" else "lb-restart.json"):
        _apply(lb, tmp_path / ".agent-lb" / via / "runtime" / "lb-restart.json", tmp_path)
    assert live_lock.read_text() == "pid=1 live\n"
    assert lb.SANDBOX_ROOT is None and lb.LOCK_FILE == lb.LIVE_BINDINGS["LOCK_FILE"]


@pytest.mark.parametrize(
    "planted", ["lb-restart.lock", "state/front.json", "watchdog.pause", "launchd/com.agent-lb.drill.sbx-r1.plist"]
)
def test_an_in_root_symlink_is_refused_before_anything_is_written(tmp_path: Path, planted: str) -> None:

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
            env={**os.environ, "HOME": str(tmp_path)},
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





def _live_state(tmp_path: Path) -> Path:

    live = tmp_path / ".agent-lb" / "state"
    live.mkdir(parents=True)
    (live / "front-preferred-port").write_text("2457\n")
    (live / "front-preferred-port.tmp").write_text("2459\n")
    (live / "lb-standby.pid").write_text("4242\n")
    return live


def test_a_state_dir_swapped_between_write_and_rename_never_replaces_the_live_preference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

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
    assert (root / "state.moved" / "front-preferred-port").read_text() == "2472\n"


def test_a_state_dir_swapped_before_pidfile_cleanup_never_unlinks_the_live_pidfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    lb = _load()
    config = _built_sandbox(tmp_path)
    root = config.parent
    live = _live_state(tmp_path)
    _apply(lb, config, tmp_path)
    monkeypatch.setattr(lb, "listener_pid", lambda port: None)
    (root / "state").symlink_to(live)
    with pytest.raises(lb.SandboxRefused):
        lb.stop_leftover_standby(0)
    assert (live / "lb-standby.pid").read_text() == "4242\n"
    with pytest.raises(lb.SandboxRefused):
        lb.front_routes_to(2471, timeout=0)


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
    lb.stop_leftover_standby(0)
    assert not (root / "state" / "lb-standby.pid").exists()


@pytest.mark.parametrize("which,mode", [("root", 0o755), ("root", 0o710), ("sandboxes", 0o777)])
def test_a_root_lb_sandbox_did_not_make_is_refused(tmp_path: Path, which: str, mode: int) -> None:

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
    _apply(lb, config, tmp_path)
    with lb.Lock("unit", 1):
        pass





SHIM_BODY = "#!/bin/sh\nlaunch" + "ctl kick" + "start -k gui/$UID/com.aneyman.agent-lb\n"


@pytest.mark.parametrize(
    "shim",
    ["link-into-root", "bin-dir-link", "group-writable", "in-root-name", "private-script", "native-elsewhere"],
)
def test_a_shim_on_the_interpreter_path_is_refused_and_never_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shim: str
) -> None:

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
    lb.start_standby(1)
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
    lb.kickstart_primary(wait=False)
    assert len(kicked) == 1 and kicked[0][-1] == f"gui/{os.getuid()}/{lb.LABEL}"
    kicked.clear()
    in_root = str(root / "runtime" / ".venv" / "bin" / "python")
    for program, arguments in (
        (in_root, [in_root, *shown["arguments"][1:]]),
        (python, [python, "/h/.agent-lb/runtime/agent-lb/.venv/bin/agent-lb", "--port", "2471"]),

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
            replaced.append(True)
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





def _job_print(
    label: str, plist: dict, program: str | None = None, arguments: list[str] | None = None, submitted: bool = False
) -> str:

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
    lines += ["", "\tproperties = inferred program"]
    if submitted:
        lines[2:4] = ["\tpath = (submitted by python3.14[4242])", "\ttype = Submitted"]
        lines += ["", "\tsubmitted job. ignore execute allowed", ""]
    lines += ["}"]
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

    lb = _load()
    config = _built_sandbox(tmp_path)
    label = f"com.agent-lb.drill.sbx-unit{os.getpid()}"
    data = json.loads(config.read_text())
    data["label"] = label
    config.write_text(json.dumps(data))
    disk = Path(data["plist"])
    clean = {**plistlib.loads(disk.read_bytes()), "Label": label}
    disk.write_bytes(plistlib.dumps(clean))
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
        lb.check_loaded_sandbox_job()
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
        "\n\t}\n\tforged = {\n\t\tDYLD_INSERT_LIBRARIES => /x/hook.dylib",
        "\n\tstderr path = /x/live.log",
        "\n\t\tDYLD_INSERT_LIBRARIES => /x/hook.dylib",
    ],
)
def test_a_value_with_a_line_break_cannot_pass_for_launchd_structure(tmp_path: Path, forged: str) -> None:

    lb = _load()
    config = _built_sandbox(tmp_path)
    _apply(lb, config, tmp_path)
    plist = plistlib.loads(Path(json.loads(config.read_text())["plist"]).read_bytes())
    python = _venv_python(tmp_path)
    want = list(plist["ProgramArguments"])
    lb.check_loaded_job_print(_job_print(lb.LABEL, plist), plist, python, want)
    text = _job_print(lb.LABEL, plist).replace(
        "\t\tPYTHONPATH => ", "\t\tAGENT_LB_X => x" + forged + "\n\t\tPYTHONPATH => "
    )
    with pytest.raises(lb.SandboxRefused):
        lb.check_loaded_job_print(text, plist, python, want)
    bad = json.loads(json.dumps(plist))
    bad["EnvironmentVariables"]["AGENT_LB_X"] = "x" + forged
    with pytest.raises(lb.SandboxRefused, match="control character"):
        lb.check_sandbox_plist(bad, config.parent, lb.LABEL, lb.PRIMARY_PORT)


def test_a_sandbox_restart_never_prints_a_live_launchd_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    lb = _load()
    _apply(lb, _built_sandbox(tmp_path), tmp_path)
    asked: list[list[str]] = []

    def launchctl(argv, *args, **kwargs):
        asked.append([str(a) for a in argv])
        return subprocess.CompletedProcess(argv, 0, "\tpid = 4242\n", "")

    monkeypatch.setattr(lb.subprocess, "run", launchctl)
    for label in ("com.aneyman.agent-lb", "com.aneyman.agent-lb-front"):
        with pytest.raises(lb.SandboxRefused):
            lb.launchd_pid(label)
        with pytest.raises(lb.SandboxRefused):
            lb.job_loaded(label)
    assert asked == [], "launchctl was asked to print a live job"
    assert lb.launchd_pid(lb.LABEL) == 4242
    assert asked == [["launchctl", "print", f"gui/{os.getuid()}/{lb.LABEL}"]]





def _fake_live_front(tmp_path: Path) -> Path:

    live = tmp_path / "live-home" / "state" / "front.json"
    live.parent.mkdir(parents=True)
    live.write_text('{"preferred": 2457}')
    return live


@pytest.mark.parametrize("swap", ["symlink", "hardlink"])
def test_a_config_swapped_for_a_link_to_live_state_is_never_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, swap: str
) -> None:

    import io

    lb = _load()
    config = _built_sandbox(tmp_path)
    live = _fake_live_front(tmp_path)
    config.unlink()
    if swap == "symlink":
        config.symlink_to(live)
    else:
        os.link(live, config)
    live_id = (live.stat().st_dev, live.stat().st_ino)
    read: list[tuple[int, int]] = []
    real_read, real_io_open = os.read, io.open

    def os_read(fd, n):
        info = os.fstat(fd)
        read.append((info.st_dev, info.st_ino))
        return real_read(fd, n)

    def io_open(file, *args, **kwargs):
        fh = real_io_open(file, *args, **kwargs)
        if hasattr(fh, "fileno"):
            info = os.fstat(fh.fileno())
            read.append((info.st_dev, info.st_ino))
        return fh

    monkeypatch.setattr(os, "read", os_read)
    monkeypatch.setattr(io, "open", io_open)
    with pytest.raises(lb.SandboxRefused):
        _apply(lb, config, tmp_path)
    monkeypatch.undo()
    assert live_id not in read, "lb-restart read live state through the sandbox config"
    assert lb.SANDBOX_ROOT is None and lb.LOCK_FILE == lb.LIVE_BINDINGS["LOCK_FILE"]
    assert live.read_text() == '{"preferred": 2457}'
    config.unlink()
    config.write_text(json.dumps(_config(tmp_path)))
    monkeypatch.setattr(os, "read", os_read)
    monkeypatch.setattr(io, "open", io_open)
    _apply(lb, config, tmp_path)
    monkeypatch.undo()
    assert (config.stat().st_dev, config.stat().st_ino) in read, "the spies never saw the config read"


def _attach_volume(image: Path, mountpoint: Path) -> list[str]:

    create = ["/usr/bin/hdiutil", "create", "-quiet", "-size", "64m", "-type", "SPARSE", "-fs", "APFS"]
    subprocess.run([*create, "-volname", "lbrestart-unit", str(image)], check=True, timeout=180)
    attach = ["/usr/bin/hdiutil", "attach", "-plist", "-nobrowse", "-noautoopen", "-owners", "on", "-mountpoint"]
    done = subprocess.run([*attach, str(mountpoint), str(image)], check=True, capture_output=True, timeout=180)
    entities = plistlib.loads(done.stdout).get("system-entities", [])
    return sorted({e["dev-entry"] for e in entities if isinstance(e.get("dev-entry"), str)}, key=len)


@pytest.mark.skipif(sys.platform != "darwin", reason="hdiutil is macOS")
def test_a_root_that_is_not_its_own_volume_is_refused_before_its_lock_is_touched(tmp_path: Path) -> None:

    import errno

    lb = _load()
    config = _built_sandbox(tmp_path)
    live = _fake_live_front(tmp_path)
    os.rename(live, config.parent / "lb-restart.lock")
    with pytest.raises(lb.SandboxRefused, match="own volume"):
        _apply(lb, config, tmp_path, own_volume=False)
        with lb.Lock("unit", 1):
            pass
    assert (config.parent / "lb-restart.lock").read_text() == '{"preferred": 2457}', "the live inode was truncated"
    assert lb.SANDBOX_ROOT is None and lb.LOCK_FILE == lb.LIVE_BINDINGS["LOCK_FILE"]

    root2 = tmp_path / ".agent-lb" / "sandboxes" / "r2"
    root2.mkdir()
    devices = _attach_volume(tmp_path / "r2.sparseimage", root2)
    try:
        config2 = _built_sandbox(tmp_path, "r2")
        _apply(lb, config2, tmp_path, own_volume=False)
        assert lb.SANDBOX_ROOT == root2
        with lb.Lock("unit", 1):
            pass
        live2 = tmp_path / "live-home" / "state" / "front2.json"
        live2.write_text('{"preferred": 2457}')
        with pytest.raises(OSError) as moved:
            os.rename(live2, root2 / "lb-restart.lock")
        assert moved.value.errno == errno.EXDEV
    finally:
        lb.unpin_sandbox_root()
        subprocess.run(["/usr/bin/hdiutil", "detach", "-quiet", "-force", str(root2)], timeout=180, check=False)
        if devices and os.path.ismount(root2):
            subprocess.run(["/usr/bin/hdiutil", "detach", "-quiet", "-force", devices[0]], timeout=180, check=False)


@pytest.mark.skipif(sys.platform != "darwin", reason="launchd is macOS")
def test_a_job_start_submitted_by_value_passes_the_loaded_job_check(tmp_path: Path) -> None:

    lb = _load()
    config = _built_sandbox(tmp_path)
    _apply(lb, config, tmp_path)
    root = config.parent
    plist = plistlib.loads(Path(lb.PLIST).read_bytes())
    python = lb.sandbox_python_for(root)
    want = [*lb.sandbox_program(root), "--host", "127.0.0.1", "--port", str(lb.PRIMARY_PORT)]
    text = _job_print(lb.LABEL, plist, program=str(python), arguments=want, submitted=True)
    lb.check_loaded_job_print(text, plist, python, want)
    flag = "\tsubmitted job. ignore execute allowed\n"
    for forged in (
        text.replace(flag, flag + flag),
        text.replace(flag, "\tsubmitted job. ignore execute allowed; DYLD_INSERT_LIBRARIES\n"),
        text.replace("\tenvironment = {\n", "\tenvironment = {\n\t" + flag),
    ):
        with pytest.raises(lb.SandboxRefused):
            lb.check_loaded_job_print(forged, plist, python, want)
