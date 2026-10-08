"""lb-sandbox guards, leak scan and _serve custody.

Unit level on purpose: the guards and the scan are pure decision tables with
many edge cases, and the live path (launchd, the live pool) is proven by
scripts/lb-sandbox-check on Studio. No test here starts a process or touches
~/.agent-lb; the one CLI test can only reach a refusal or a missing sandbox.
"""

from __future__ import annotations

import base64
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

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "lb-sandbox"
FAKE_TOKEN = "lbsbx-unit-fixture-not-a-credential-0123456789abcdefghijklmnopqrstuvwxyz"
SERVE_PORTS = {
    "gate": 2476,
    "edge_anthropic": 2477,
    "edge_openai": 2478,
    "front": 2480,
    "primary": 2481,
    "standby": 2482,
}


def _load() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("lb_sandbox_under_test", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _install_venv(home_lb: Path, runnable: bool = False) -> Path:
    """<home_lb>/runtime/agent-lb/.venv as interpreter_for names it for roots under <home_lb>/sandboxes.

    runnable: a real venv dir whose bin/python leads to the interpreter running this test, with this test
    venv's packages (the integration test execs it). Otherwise bin/python leads to a private stand-in that starts
    like a native executable (the guards only inspect it; the box's interpreter may be group-writable). Either
    way pyvenv.cfg names the dir bin/python leads to as the venv's home, as a real venv's does."""
    venv = home_lb / "runtime" / "agent-lb" / ".venv"
    python = venv / "bin" / "python"
    if not os.path.lexists(python):
        python.parent.mkdir(parents=True, exist_ok=True)
        if runnable:
            real = Path(os.path.realpath(sys.executable))
            python.symlink_to(real)
            cfg = Path(sys.prefix) / "pyvenv.cfg"
            lines = cfg.read_text().splitlines() if cfg.is_file() else []
            lines = [line for line in lines if line.partition("=")[0].strip().lower() != "home"]
            (venv / "pyvenv.cfg").write_text("\n".join([f"home = {real.parent}", *lines]) + "\n")
            (venv / "pyvenv.cfg").chmod(0o644)  # what a venv writes, whatever the umask
            (venv / "lib").symlink_to(Path(sys.prefix) / "lib", target_is_directory=True)
        else:
            base = home_lb / "base-python" / "python3"
            base.parent.mkdir(exist_ok=True)
            base.parent.chmod(0o755)
            with open(os.path.realpath(sys.executable), "rb") as fh:
                base.write_bytes(fh.read(4) + bytes(60))
            base.chmod(0o755)
            (venv / "pyvenv.cfg").write_text(f"home = {base.parent}\nversion_info = 3.14\n")
            (venv / "pyvenv.cfg").chmod(0o644)  # what a venv writes, whatever the umask
            python.symlink_to(base)
    return python


def _use_bootstrap(lb: ModuleType, home: Path) -> Path:
    """Point lb.bootstrap_script at a private copy of lb-sandbox outside the sandboxes, as the installed file
    is (a checkout may be group-writable). Returns its path."""
    path = home / "installed-bin" / "lb-sandbox"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(SCRIPT.read_bytes())
        path.chmod(0o755)
    lb.bootstrap_script = lambda: path
    return path


def _key(root: Path) -> None:
    """The store key start creates: data/encryption.key, 0600, one link (_serve refuses to boot without it)."""
    (root / "data").mkdir(parents=True, exist_ok=True)
    key = root / "data" / "encryption.key"
    key.write_bytes(b"k" * 44)
    key.chmod(0o600)
    root.parent.chmod(0o700)  # start makes the sandboxes dir 0700 whatever the umask; the guards require it


@pytest.mark.parametrize(
    ("check", "value"),
    [
        ("run_id", "Upper"),
        ("run_id", "-leading-dash"),
        ("run_id", "has_underscore"),
        ("run_id", "a" * 81),
        ("run_id", "../escape"),
        ("run_id", "r1-aux"),  # would own run r1's aux label
        ("label", "com.aneyman.agent-lb"),
        ("label", "com.agent-lb.drill."),
        ("label", "com.agent-lb.drill.x/y"),
        ("ports", [2455]),
        ("ports", [2457]),
        ("ports", [2459]),
        ("ports", [1455]),
        ("ports", [2469]),
        ("ports", [2600]),
        ("ports", [70000]),
        ("ports", [True]),
        ("ports", [2480, 2480]),
    ],
)
def test_guards_refuse(check: str, value) -> None:
    lb = _load()
    guard = {"run_id": lb.check_run_id, "label": lb.check_label, "ports": lb.check_ports}[check]
    with pytest.raises(lb.Refused):
        guard(value)


def test_guards_accept_a_sandbox() -> None:
    lb = _load()
    assert lb.check_run_id("lbsbx-20261007t170000z-42") == "lbsbx-20261007t170000z-42"
    assert lb.labels_for("r1") == ("com.agent-lb.drill.sbx-r1", "com.agent-lb.drill.sbx-r1-aux")
    assert lb.check_ports([2470, 2471, 2599]) == [2470, 2471, 2599]


@pytest.mark.parametrize("relative", ["sandboxes", "elsewhere/r1", "sandboxes/r1/nested", "sandboxes/../r1"])
def test_root_must_sit_directly_under_sandboxes(tmp_path: Path, relative: str) -> None:
    lb = _load()
    with pytest.raises(lb.Refused):
        lb.check_root(tmp_path / relative, sandboxes=tmp_path / "sandboxes")
    assert lb.check_root(tmp_path / "sandboxes" / "r1", sandboxes=tmp_path / "sandboxes").name == "r1"


@pytest.mark.parametrize(
    ("cmd", "owned", "named"),
    [
        ("/h/.agent-lb/sandboxes/r1/runtime/.venv/bin/python -m app.cli --port 2471", True, False),
        ("node /h/.agent-lb/sandboxes/r1/runtime/scripts/agent-lb-front.mjs /h/.agent-lb/sandboxes/r1", True, False),
        ("python lb-restart --reap 9 210 --sandbox /h/.agent-lb/sandboxes/r1/lb-restart.json", True, False),
        ("/bin/launchctl print gui/501/com.agent-lb.drill.sbx-r1", True, True),
        ("/bin/launchctl bootout gui/501/com.agent-lb.drill.sbx-r1-aux", True, True),
        ("python lb-sandbox status --run-id r1", False, True),
        ("python lb-sandbox status --run-id=r1", False, True),
        ("/h/.agent-lb/sandboxes/r10/runtime/.venv/bin/python -m app.cli --port 2481", False, False),
        ("node agent-lb-front.mjs /h/.agent-lb/sandboxes/r1-b", False, False),
        ("/bin/launchctl print gui/501/com.agent-lb.drill.sbx-r10", False, False),
        ("python lb-sandbox status --run-id r10", False, False),
        ("python worker.py r1 /tmp/r1/x", False, False),
        ("/h/.agent-lb/runtime/agent-lb/.venv/bin/agent-lb --host 127.0.0.1 --port 2457", False, False),
    ],
)
def test_stop_only_claims_processes_of_its_own_root(cmd: str, owned: bool, named: bool) -> None:
    """owned: what stop may signal (the root path or the run's label). named: what it reports or inspects."""
    lb = _load()
    assert lb.owned_process(cmd, Path("/h/.agent-lb/sandboxes/r1"), "r1") is owned
    assert lb.names_run(cmd, "r1") is named


LIVE_PRIMARY_CMD = (
    "/Users/aneyman/.agent-lb/runtime/agent-lb/.venv/bin/python "
    "/Users/aneyman/.agent-lb/runtime/agent-lb/.venv/bin/agent-lb --host 127.0.0.1 --port 2457"
)


@pytest.mark.parametrize("run_id", ["aneyman", "runtime", "agent-lb", "bin", "python", "host"])
def test_a_run_id_that_is_a_word_of_the_live_command_never_claims_it(run_id: str) -> None:
    """Finding: stop killed any process whose command line held the run id as a word. `stop --run-id aneyman`
    (or runtime, agent-lb) matched the live agent-lb primary and, on Studio, 911 other processes."""
    lb = _load()
    root = Path("/Users/aneyman/.agent-lb/sandboxes") / run_id
    if run_id in lb.RESERVED_RUN_IDS:
        # Since S1-3 a run id naming a live agent-lb directory is refused outright: it cannot even be matched.
        with pytest.raises(lb.Refused):
            lb.owned_process(LIVE_PRIMARY_CMD, root, run_id)
        return
    assert lb.owned_process(LIVE_PRIMARY_CMD, root, run_id) is False
    assert lb.names_run(LIVE_PRIMARY_CMD, run_id) is False


def test_cli_refuses_a_launchctl_shim(tmp_path: Path) -> None:
    shim = tmp_path / "bin" / "launchctl"
    shim.parent.mkdir()
    shim.write_text("#!/bin/sh\nexit 0\n")
    shim.chmod(0o755)
    env = {**os.environ, "PATH": f"{shim.parent}:{os.environ['PATH']}", "LB_SANDBOX_NO_REEXEC": "1"}
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "restart", "--run-id", "lbsbx-unit-no-such-run", "--reason", "unit"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 2
    assert "not /bin/launchctl" in json.loads(proc.stdout)["refused"]


def _embedded(encode, shift: int) -> bytes:
    """The token inside a longer base64 blob, starting `shift` bytes into the encoded payload."""
    return encode(b"x" * shift + b'{"access_token": "' + FAKE_TOKEN.encode() + b'", "n": 1}')


@pytest.mark.parametrize(
    "content",
    [
        f"log line Authorization: Bearer {FAKE_TOKEN} end".encode(),
        base64.b64encode(FAKE_TOKEN.encode()),
        _embedded(base64.b64encode, 0),
        _embedded(base64.b64encode, 1),
        _embedded(base64.b64encode, 2),
        _embedded(base64.urlsafe_b64encode, 1),
        _embedded(base64.urlsafe_b64encode, 2),
    ],
)
def test_scan_finds_a_planted_token_raw_and_base64(tmp_path: Path, content: bytes) -> None:
    lb = _load()
    (tmp_path / "clean.log").write_text("nothing to see\n" * 1000)
    planted = tmp_path / "deep" / "dir" / "leak.bin"
    planted.parent.mkdir(parents=True)
    planted.write_bytes(b"\0" * 5_000_000 + content + b"\n")  # straddles the 4 MiB chunk boundary region
    result = lb.scan_paths([tmp_path], [FAKE_TOKEN])
    assert result["found"] is True
    assert result["hits"] == [str(planted.resolve())]
    assert result["files_scanned"] == 2


def test_scan_skips_excluded_symlinked_and_older_files(tmp_path: Path) -> None:
    lb = _load()
    top = tmp_path / "scan"
    top.mkdir()
    old = top / "old.log"
    old.write_text(FAKE_TOKEN)
    os.utime(old, (1, 1))
    excluded = top / "own-store"
    excluded.mkdir()
    (excluded / "copy").write_text(FAKE_TOKEN)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak").write_text(FAKE_TOKEN)
    (top / "link").symlink_to(outside, target_is_directory=True)
    (top / "file-link").symlink_to(outside / "leak")
    result = lb.scan_paths([top], [FAKE_TOKEN], newer_than=100.0, exclude=[excluded])
    assert (result["found"], result["files_scanned"]) == (False, 0)
    unfiltered = lb.scan_paths([top], [FAKE_TOKEN])  # the filters, not an empty tree, kept it clean
    assert sorted(unfiltered["hits"]) == sorted(str(p.resolve()) for p in (old, excluded / "copy"))


def test_serve_keeps_the_token_out_of_the_exec_env_and_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lb = _load()
    sandboxes = tmp_path / "sandboxes"
    root = sandboxes / "r1"
    _key(root)
    (root / "sandbox.json").write_text(json.dumps({"run_id": "r1", "ports": SERVE_PORTS}))
    live_plist = tmp_path / "live.plist"
    live_plist.write_bytes(plistlib.dumps({"EnvironmentVariables": {"AGENT_LB_FEDERATION_TOKEN": FAKE_TOKEN}}))
    monkeypatch.setattr(lb, "SANDBOXES", sandboxes)
    monkeypatch.setattr(lb, "LIVE_PLIST", live_plist)
    monkeypatch.setenv("LB_SANDBOX_ROOT", str(root.resolve()))
    monkeypatch.setenv("AGENT_LB_DATA_DIR", str(root.resolve() / "data"))
    monkeypatch.delenv("AGENT_LB_FEDERATION_TOKEN", raising=False)
    _install_venv(tmp_path)
    _use_bootstrap(lb, tmp_path)
    reads_fake_plist = lb.live_federation_token() == FAKE_TOKEN
    assert reads_fake_plist, "the test must never read the live plist"
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))

    result = lb.serve_exec_args(root, ["--host", "127.0.0.1", "--port", "2482"])
    python, argv, env = result[0], result[1], result[2]

    # Booleans only: a failing comparison must never print a token value.
    token_in_env = any(FAKE_TOKEN in f"{k}={v}" for k, v in env.items()) or "AGENT_LB_FEDERATION_TOKEN" in env
    assert not token_in_env, "the token reached the exec environment (ps eww shows it)"
    token_in_argv = any(FAKE_TOKEN in arg for arg in [python, *argv])
    assert not token_in_argv, "the token reached argv"
    handed_over = len(result) == 4 and result[3] == FAKE_TOKEN
    assert handed_over, "serve_exec_args must hand the token back for the pipe"
    assert argv[-2:] == ["--port", "2482"]  # lb-restart finds its standby by "--port <standby>"
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before
    written = [
        p for p in tmp_path.rglob("*") if p.is_file() and p != live_plist and FAKE_TOKEN.encode() in p.read_bytes()
    ]
    assert written == []


FAKE_APP = {
    "app/__init__.py": "",
    "app/core/__init__.py": "",
    "app/core/config/__init__.py": "",
    "app/core/config/settings.py": (
        "import os\n"
        "class _S:\n"
        "    def __init__(self):\n"
        "        self.federation_token = os.environ.get('AGENT_LB_FEDERATION_TOKEN')\n"
        "_cached = None\n"
        "def get_settings():\n"
        "    global _cached\n"
        "    if _cached is None:\n"
        "        _cached = _S()\n"
        "    return _cached\n"
    ),
    # Stands in for app.cli: reports what it holds (booleans and a digest, never the value), then waits.
    "app/cli.py": (
        "import hashlib, json, os, sys, time\n"
        "from pathlib import Path\n"
        "def main(argv=None):\n"
        "    root = Path(os.environ['LB_SANDBOX_ROOT'])\n"
        "    try:\n"
        "        from app.core.config.settings import get_settings\n"
        "        held = get_settings().federation_token\n"
        "    except Exception:\n"
        "        held = None\n"
        "    report = {'pid': os.getpid(), 'argv': list(argv if argv is not None else sys.argv[1:]),\n"
        "              'token_in_environ': 'AGENT_LB_FEDERATION_TOKEN' in os.environ,\n"
        "              'settings_sha': hashlib.sha256((held or '').encode()).hexdigest()}\n"
        "    swap = root / 'swap-data-to'\n"
        "    if swap.exists():  # an attacker inside the run: data/ swapped for a link once the app is up\n"
        "        (root / 'data').rename(root / 'data-aside')\n"
        "        (root / 'data').symlink_to(swap.read_text(), target_is_directory=True)\n"
        "        (Path(os.environ['AGENT_LB_DATA_DIR']) / 'app-touched').write_text('the app opened its store here')\n"
        "    (root / 'boot-report.json').write_text(json.dumps(report))\n"
        "    deadline = time.time() + 30\n"
        "    while time.time() < deadline and not (root / 'stop').exists():\n"
        "        time.sleep(0.05)\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    ),
}


def _fake_sandbox(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A sandbox root with a stand-in app, and the real lb-sandbox installed outside it (tmp/installed-bin, what
    _serve and _boot run); returns (sandboxes, root, live plist)."""
    sandboxes = tmp_path / "sandboxes"
    root = sandboxes / "r1"
    for sub in ("data", "runtime", "logs", "state", "home"):
        (root / sub).mkdir(parents=True)
    (root / "sandbox.json").write_text(json.dumps({"run_id": "r1", "ports": SERVE_PORTS, "transport": "http"}))
    _key(root)
    installed = tmp_path / "installed-bin" / "lb-sandbox"
    installed.parent.mkdir()
    installed.write_bytes(SCRIPT.read_bytes())
    installed.chmod(0o755)
    for rel, text in FAKE_APP.items():
        (root / "runtime" / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / "runtime" / rel).write_text(text)
    (root / "runtime" / ".venv").symlink_to(Path(sys.prefix), target_is_directory=True)
    _install_venv(tmp_path, runnable=True)  # what _serve execs: the installed venv's python, never the root's
    live_plist = tmp_path / "live.plist"
    live_plist.write_bytes(plistlib.dumps({"EnvironmentVariables": {"AGENT_LB_FEDERATION_TOKEN": FAKE_TOKEN}}))
    return sandboxes, root.resolve(), live_plist


# The mountpoint directory of each mounted_root, as start records it (identity before the volume covers it).
MOUNT_IDS: dict[str, os.stat_result] = {}


@pytest.fixture
def mounted_root(tmp_path: Path):
    """tmp_path/sandboxes/r1 as its own volume (a small sparse image), as start makes a root; detached after."""
    root = tmp_path / "sandboxes" / "r1"
    root.mkdir(parents=True)
    MOUNT_IDS[str(root)] = root.stat()
    image = tmp_path / "volume.sparseimage"
    create = ["/usr/bin/hdiutil", "create", "-quiet", "-size", "64m", "-type", "SPARSE", "-fs", "APFS"]
    subprocess.run([*create, "-volname", "lbsbx-unit", str(image)], check=True, timeout=180)
    attach = ["/usr/bin/hdiutil", "attach", "-plist", "-nobrowse", "-noautoopen", "-owners", "on", "-mountpoint"]
    done = subprocess.run([*attach, str(root), str(image)], check=True, capture_output=True, timeout=180)
    entities = plistlib.loads(done.stdout).get("system-entities", [])
    devices = sorted({e["dev-entry"] for e in entities if isinstance(e.get("dev-entry"), str)}, key=len)
    try:
        yield root
    finally:
        # By device, wherever the mount is now: a test may rename the sandboxes dir around a live mount, and
        # hdiutil then reports the volume busy; umount -f by its current path frees it.
        subprocess.run(["/usr/bin/hdiutil", "detach", "-quiet", "-force", str(root)], timeout=180, check=False)
        mounts = subprocess.run(["/sbin/mount"], capture_output=True, text=True, timeout=60).stdout.splitlines()
        for line in mounts:
            for device in devices:
                if line.startswith(f"{device} on "):
                    where = line[len(device) + 4 :].rsplit(" (", 1)[0]
                    subprocess.run(["/sbin/umount", "-f", where], capture_output=True, timeout=60, check=False)
        if devices:
            subprocess.run(["/usr/bin/hdiutil", "detach", "-quiet", "-force", devices[0]], timeout=180, check=False)


def _ps_env(pid: int) -> bytes:
    return subprocess.run(["/bin/ps", "-E", "-ww", "-o", "command=", "-p", str(pid)], capture_output=True).stdout


@pytest.mark.skipif(sys.platform != "darwin", reason="ps -E semantics, hdiutil and Seatbelt are macOS")
def test_serve_hands_the_token_over_a_pipe_never_through_ps_eww(tmp_path: Path, mounted_root: Path) -> None:
    """Integration: the real _serve execs the real _boot, which starts a stand-in app in the same process.

    `ps eww` (here `ps -E`) of that process must show its environment (the control) and never the token;
    the app's settings must hold it while os.environ, which children inherit, must not. The root is its own
    volume, as start makes it, and the app runs under the root's Seatbelt profile.
    """
    import hashlib
    import time

    sandboxes, root, live_plist = _fake_sandbox(tmp_path)
    driver = (
        "import importlib.machinery, importlib.util, sys\n"
        "from pathlib import Path\n"
        f"loader = importlib.machinery.SourceFileLoader('lbs', {str(tmp_path / 'installed-bin' / 'lb-sandbox')!r})\n"
        "spec = importlib.util.spec_from_loader('lbs', loader)\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "loader.exec_module(m)\n"
        f"m.SANDBOXES = Path({str(sandboxes)!r})\n"
        f"m.LIVE_PLIST = Path({str(live_plist)!r})\n"
        f"sys.exit(m.cmd_serve({str(root)!r}, ['--host', '127.0.0.1', '--port', '2481']))\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "AGENT_LB_FEDERATION_TOKEN"}
    env["LB_SANDBOX_ROOT"] = str(root)
    proc = subprocess.Popen([sys.executable, "-c", driver], env=env, stderr=subprocess.PIPE)
    report_path = root / "boot-report.json"
    try:
        deadline = time.monotonic() + 30
        while not report_path.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert report_path.exists(), f"the app never started: exit {proc.poll()}"
        report = json.loads(report_path.read_text())
        shown = _ps_env(report["pid"])
        assert f"LB_SANDBOX_ROOT={root}".encode() in shown, "control: ps must show this process's environment"
        leaked = FAKE_TOKEN.encode() in shown
        assert not leaked, "ps eww of the app process shows the federation token"
        procs = _load().scan_processes(root, "r1", [FAKE_TOKEN])
        confined = _load().is_confined(report["pid"])
    finally:
        (root / "stop").touch()
        try:
            proc.wait(30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    assert report["pid"] == proc.pid, "the app must run in the exec'd process itself, no second exec"
    assert confined is True, "the app must run under the root's Seatbelt profile"
    assert report["settings_sha"] == hashlib.sha256(FAKE_TOKEN.encode()).hexdigest()
    assert report["token_in_environ"] is False, "children of the app would inherit the token"
    assert report["argv"][-2:] == ["--port", "2481"]
    assert (procs["found"], proc.pid in procs["visible_pids"], proc.pid in procs["pids"]) == (False, True, True)


@pytest.mark.skipif(sys.platform != "darwin", reason="ps -E semantics are macOS")
def test_process_scan_finds_a_token_in_an_exec_environment(tmp_path: Path) -> None:
    """The control for the scan above: a run process started with the token in its env is a hit."""
    import time

    lb = _load()
    root = (tmp_path / "sandboxes" / "r1").resolve()
    root.mkdir(parents=True)
    env = {"PATH": "/usr/bin:/bin", "LB_SANDBOX_ROOT": str(root), "AGENT_LB_FEDERATION_TOKEN": FAKE_TOKEN}
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)", str(root)], env=env)
    try:
        time.sleep(0.5)
        result = lb.scan_processes(root, "r1", [FAKE_TOKEN])
    finally:
        proc.kill()
        proc.wait()
    assert (result["found"], result["hits"], result["complete"]) == (True, [proc.pid], True)


@pytest.mark.parametrize(
    "argv",
    [
        ["--host", "0.0.0.0", "--port", "2482"],
        ["--port", "2457"],
        ["--port", "2490"],  # not this sandbox's primary or standby
        ["--port", "2482", "--ssl-keyfile", "/tmp/k"],  # nothing but host and port reaches the app
    ],
)
def test_serve_refuses_other_hosts_and_ports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv) -> None:
    lb = _load()
    sandboxes = tmp_path / "sandboxes"
    root = sandboxes / "r1"
    _key(root)
    (root / "sandbox.json").write_text(json.dumps({"run_id": "r1", "ports": SERVE_PORTS}))
    monkeypatch.setattr(lb, "SANDBOXES", sandboxes)
    monkeypatch.setenv("LB_SANDBOX_ROOT", str(root.resolve()))
    monkeypatch.setenv("AGENT_LB_DATA_DIR", str(root.resolve() / "data"))
    with pytest.raises(lb.Refused):
        lb.serve_exec_args(root, argv)


class _EdgeRig:
    """A real upstream SSE server and the real edge handler on 127.0.0.1, in a background event loop.

    Integration over HTTP on purpose: the truncation contract is about bytes on a socket, which only a
    real transport under backpressure can show. Only the vendor URL is replaced by the local upstream.
    """

    def __init__(
        self,
        lb: ModuleType,
        fault_file: Path,
        body: bytes,
        break_after: int | None,
        edge: str = "anthropic",
        content_type: str | None = "text/event-stream",
    ) -> None:
        import asyncio
        import threading

        self.lb, self.fault_file, self.body, self.break_after = lb, fault_file, body, break_after
        self.edge, self.content_type = edge, content_type
        self.entries: list[dict] = []
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.edge_port = asyncio.run_coroutine_threadsafe(self._start(), self.loop).result(30)

    async def _start(self) -> int:
        import aiohttp
        from aiohttp import web

        async def upstream(request: web.Request) -> web.StreamResponse:
            headers = {"content-type": self.content_type} if self.content_type else {}
            response = web.StreamResponse(status=200, headers=headers)
            await response.prepare(request)
            for start in range(0, len(self.body), 65536):
                if self.break_after is not None and start >= self.break_after:
                    request.transport.abort()  # the vendor connection dies mid-body
                    return response
                await response.write(self.body[start : start + 65536])
            await response.write_eof()
            return response

        self.runners = []
        up = web.Application()
        up.router.add_route("*", "/{tail:.*}", upstream)
        up_port = await self._serve(up)
        self.session = aiohttp.ClientSession(auto_decompress=False)
        counters = {"edge_requests": {self.edge: 0}, "faults_applied": {self.edge: 0}}
        handler = self.lb.make_edge_handler(
            self.edge,
            f"http://127.0.0.1:{up_port}",
            self.lb.FaultState(self.fault_file.parent, self.fault_file.name),
            self.session,
            counters,
            self.entries.append,
        )
        edge = web.Application()
        edge.router.add_route("*", "/{tail:.*}", handler)
        return await self._serve(edge)

    async def _serve(self, app) -> int:
        from aiohttp import web

        # auto_decompress=False, as cmd_aux serves the edges: the edge gets the request body as agent-lb sent it.
        runner = web.AppRunner(app, auto_decompress=False, access_log=None, handle_signals=False)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        self.runners.append(runner)
        return site._server.sockets[0].getsockname()[1]

    def fetch(
        self, wait_before_reading: float, path: str = "/v1/messages", body: bytes = b'{"stream": true}', gzipped=False
    ) -> tuple[bytes, bool]:
        """POST through the edge; returns the body bytes received and whether the body ended cleanly.

        gzipped: send the body as agent-lb sends a large one upstream (Content-Encoding: gzip)."""
        import gzip
        import http.client
        import time

        conn = http.client.HTTPConnection("127.0.0.1", self.edge_port, timeout=30)
        headers = {"content-type": "application/json", **({"content-encoding": "gzip"} if gzipped else {})}
        conn.request("POST", path, body=gzip.compress(body) if gzipped else body, headers=headers)
        resp = conn.getresponse()
        time.sleep(wait_before_reading)  # let the edge queue bytes the client has not read: backpressure
        received = b""
        try:
            while chunk := resp.read1(8192):
                received += chunk
                time.sleep(0.0005)
            clean = True
        except (http.client.IncompleteRead, ConnectionError):
            clean = False
        conn.close()
        return received, clean

    def close(self) -> None:
        import asyncio

        async def stop() -> None:
            await self.session.close()
            for runner in self.runners:
                await runner.cleanup()

        asyncio.run_coroutine_threadsafe(stop(), self.loop).result(30)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(10)


SSE_BODY = b"".join(b'data: {"n": %d, "pad": "%s"}\n\n' % (i, b"x" * 200) for i in range(20000))


def test_edge_cut_delivers_exactly_n_bytes_under_backpressure_then_truncates(tmp_path: Path) -> None:
    """The cut_after_bytes contract: exactly N body bytes reach a slow reader, then the body ends unframed.

    With aiohttp 3.14 the transport queue was measured empty at the cut, so this does not tell
    close() from abort(); it guards the byte count and the truncation, which nothing else covers.
    """
    lb = _load()
    cut_at = 3_000_001
    fault_file = tmp_path / "fault-anthropic.json"
    fault_file.write_text(json.dumps({"nonce": "n1", "armed": True, "fault": "cut_after_bytes", "n": cut_at}))
    rig = _EdgeRig(lb, fault_file, SSE_BODY, break_after=None)
    try:
        received, clean = rig.fetch(wait_before_reading=1.0)
    finally:
        rig.close()
    assert (len(received), clean) == (cut_at, False)
    assert received == SSE_BODY[:cut_at]
    assert [(e["fault"], e["bytes"]) for e in rig.entries] == [(f"cut_after_bytes:{cut_at}", cut_at)]


def test_edge_passes_an_upstream_break_through_as_a_truncation(tmp_path: Path) -> None:
    lb = _load()
    rig = _EdgeRig(lb, tmp_path / "no-fault.json", SSE_BODY, break_after=262144)
    try:
        received, clean = rig.fetch(wait_before_reading=0.2)
    finally:
        rig.close()
    assert clean is False, "an upstream break must not reach the client as a clean end of body"
    assert 0 < len(received) < len(SSE_BODY)
    assert rig.entries[0].get("error")
    # Control: with no break the same rig ends cleanly (the assertion above is about the break, not the rig).
    rig = _EdgeRig(lb, tmp_path / "no-fault.json", SSE_BODY, break_after=None)
    try:
        whole, whole_clean = rig.fetch(wait_before_reading=0.0)
    finally:
        rig.close()
    assert (len(whole), whole_clean) == (len(SSE_BODY), True)


def test_scan_reports_an_unreadable_directory_as_incomplete(tmp_path: Path) -> None:
    lb = _load()
    top = tmp_path / "logs"
    locked = top / "locked"
    locked.mkdir(parents=True)
    (top / "clean.log").write_text("clean\n")
    (locked / "hidden.log").write_text(FAKE_TOKEN)
    locked.chmod(0)
    try:
        result = lb.scan_paths([top], [FAKE_TOKEN])
        stale = lb.scan_paths([top], [FAKE_TOKEN], newer_than=locked.stat().st_mtime + 1)
    finally:
        locked.chmod(0o755)
    assert (result["found"], result["complete"], result["unreadable"]) == (False, False, 1)
    # Files inside can change without touching the directory's mtime, so newer-than never excuses it.
    assert (stale["complete"], stale["unreadable"]) == (False, 1)
    assert lb.scan_paths([top], [])["complete"] is False  # no secret loaded is never a clean scan


def test_secret_loading_refuses_a_missing_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lb = _load()
    no_token = tmp_path / "no-token.plist"
    no_token.write_bytes(plistlib.dumps({"EnvironmentVariables": {}}))
    with_token = tmp_path / "live.plist"
    with_token.write_bytes(plistlib.dumps({"EnvironmentVariables": {"AGENT_LB_FEDERATION_TOKEN": FAKE_TOKEN}}))
    root = tmp_path / "r1"
    (root / "data").mkdir(parents=True)  # a sandbox root whose store was never written
    monkeypatch.setattr(lb, "LIVE_PLIST", no_token)
    with pytest.raises(lb.Refused):
        lb.load_secrets(None)
    monkeypatch.setattr(lb, "LIVE_PLIST", with_token)
    with pytest.raises(lb.Refused):
        lb.load_secrets(root)
    assert len(lb.load_secrets(None)) == 1


def test_log_export_copies_only_scanned_regular_files(tmp_path: Path) -> None:
    lb = _load()
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "edge.jsonl").write_text('{"ok": 1}\n')
    outside = tmp_path / "outside-secret"
    outside.write_text(FAKE_TOKEN)
    (logs / "link.log").symlink_to(outside)
    (logs / "dir-link").symlink_to(tmp_path, target_is_directory=True)
    scanned = lb.scan_paths([logs], [FAKE_TOKEN])
    dest = tmp_path / "export"
    copied = lb.export_logs(tmp_path, dest, scanned["_files"], lb.Detector([FAKE_TOKEN]))
    assert (scanned["found"], scanned["complete"], copied["files"]) == (False, True, 1)
    assert sorted(p.name for p in dest.rglob("*")) == ["edge.jsonl"]


def test_sandbox_env_is_a_valid_app_config_with_the_dashboard_locked(tmp_path: Path) -> None:
    """The app's own Settings must accept the sandbox env (a rejected env only shows as a start timeout)."""
    lb = _load()
    root = tmp_path / "sandboxes" / "r1"
    env = lb.sandbox_env(root, SERVE_PORTS, "r1", "http")
    probe = (
        "from app.core.config.settings import Settings; s = Settings(); "
        "print(s.dashboard_auth_mode.value, s.firewall_trust_proxy_headers, ','.join(s.firewall_trusted_proxy_cidrs)); "
        "from uvicorn.middleware.proxy_headers import _TrustedHosts; import os; "
        "print('127.0.0.1' in _TrustedHosts(os.environ['FORWARDED_ALLOW_IPS']))"
    )
    repo = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env={**env, "PATH": os.environ["PATH"], "PYTHONPATH": str(repo)},
        cwd=repo,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    # The last field: uvicorn does not take X-Forwarded-For from a loopback peer (no forged proxy address).
    assert proc.stdout.split() == ["trusted_header", "True", "192.0.2.1/32", "False"]


@pytest.mark.parametrize("shift", [0, 1, 2])
def test_scan_finds_a_short_secret_inside_base64(tmp_path: Path, shift: int) -> None:
    lb = _load()
    short = "k5Zq!x"  # base64 fragments this short were once dropped as too likely to match by chance
    (tmp_path / "blob").write_bytes(base64.b64encode(b"x" * shift + short.encode() + b"tail"))
    assert lb.scan_paths([tmp_path], [short])["found"] is True


def test_scan_reports_a_named_path_it_cannot_stat(tmp_path: Path) -> None:
    lb = _load()
    locked = tmp_path / "locked"
    (locked / "inner").mkdir(parents=True)
    (locked / "inner" / "leak.log").write_text(FAKE_TOKEN)
    locked.chmod(0)
    try:
        result = lb.scan_paths([locked / "inner"], [FAKE_TOKEN])
    finally:
        locked.chmod(0o755)
    assert (result["complete"], result["unreadable"], result["files_scanned"]) == (False, 1, 0)


@pytest.mark.parametrize("redirect", ["gate", "edge_anthropic", "edge_openai", "front", "standby"])
def test_serve_validates_all_metadata_ports_before_credentials(tmp_path, monkeypatch, redirect):
    lb = _load()
    root = tmp_path / "sandboxes" / "r1"
    root.mkdir(parents=True)
    ports = dict(SERVE_PORTS, **{redirect: 2455})
    (root / "sandbox.json").write_text(json.dumps({"run_id": "r1", "ports": ports}))
    monkeypatch.setattr(lb, "SANDBOXES", root.parent)
    monkeypatch.setenv("LB_SANDBOX_ROOT", str(root))
    with pytest.raises(lb.Refused):
        lb.serve_exec_args(root, ["--port", "2481"])


@pytest.mark.parametrize("relative", ["data", "data/store.db", "data/encryption.key"])
def test_serve_refuses_redirected_data_before_credentials(tmp_path, monkeypatch, relative):
    lb = _load()
    root = tmp_path / "sandboxes" / "r1"
    (root / "data").mkdir(parents=True)
    (root / "sandbox.json").write_text(json.dumps({"run_id": "r1", "ports": SERVE_PORTS}))
    outside = tmp_path / "live"
    outside.mkdir()
    path = root / relative
    if path.is_dir():
        path.rmdir()
    path.symlink_to(outside)
    monkeypatch.setattr(lb, "SANDBOXES", root.parent)
    monkeypatch.setenv("LB_SANDBOX_ROOT", str(root))
    with pytest.raises(lb.Refused):
        lb.serve_exec_args(root, ["--port", "2481"])


@pytest.mark.parametrize("link", ["file", "directory", "parent", "hardlink"])
def test_log_export_never_truncates_redirected_destination(tmp_path, link):
    lb = _load()
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "primary.log").write_text("safe log")
    live = tmp_path / "live"
    live.mkdir()
    target = live / "primary.log"
    target.write_text("live sentinel")
    dest = tmp_path / "export"
    dest.mkdir()
    if link == "file":
        (dest / "primary.log").symlink_to(target)
    elif link == "hardlink":
        os.link(target, dest / "primary.log")
    elif link == "directory":
        dest.rmdir()
        dest.symlink_to(live)
    else:
        parent = tmp_path / "parent-link"
        parent.symlink_to(live)
        dest = parent / "export"
    with pytest.raises(OSError):
        lb.export_logs(tmp_path, dest, [str(logs / "primary.log")], lb.Detector([FAKE_TOKEN]))
    assert target.read_text() == "live sentinel"


MIRRORED_TOKEN = "lbsbx-unit-mirrored-access-not-a-credential-abcdefghijklmnopqrstuvwxyz0123"


def _keyed_store(lb: ModuleType, root: Path) -> None:
    """A sandbox store as the app writes it: one account, its access token encrypted with the root's key."""
    import sqlite3

    from cryptography.fernet import Fernet

    (root / "data").mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    (root / "data" / "encryption.key").write_bytes(key)
    (root / "data" / "encryption.key").chmod(0o600)
    fernet = Fernet(key)
    con = sqlite3.connect(root / "data" / "store.db")
    con.execute(
        "CREATE TABLE accounts (id TEXT, provider TEXT, status TEXT, access_expires_at TEXT,"
        " access_token_encrypted BLOB, id_token_encrypted BLOB, reset_at REAL, blocked_at TEXT,"
        " refresh_token_encrypted BLOB)"
    )
    con.execute(
        "INSERT INTO accounts VALUES ('a1', 'anthropic', 'active', '2099-01-01T00:00:00', ?, NULL, NULL, NULL, ?)",
        (fernet.encrypt(MIRRORED_TOKEN.encode()), fernet.encrypt(b"")),
    )
    con.commit()
    con.close()


def test_store_key_is_created_private_and_exclusive(tmp_path: Path) -> None:
    lb = _load()
    key = tmp_path / "encryption.key"
    dir_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        made = lb.create_key(dir_fd)
        assert (oct(key.stat().st_mode & 0o777), key.stat().st_nlink, len(key.read_bytes())) == ("0o600", 1, 44)
        assert made == lb.identity(key.stat()), "the recorded identity is the key's own"
        with pytest.raises(FileExistsError):
            lb.create_key(dir_fd)  # never reuses a key someone placed there
        planted = tmp_path / "planted.key"
        (tmp_path / "link.key").symlink_to(planted)
        with pytest.raises(OSError):
            lb.create_key(dir_fd, "link.key")
        assert not planted.exists()
    finally:
        os.close(dir_fd)


@pytest.mark.parametrize("copied", ["data/store.db", "data/encryption.key"])
def test_a_copy_of_the_store_or_its_key_outside_custody_is_a_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, copied: str
) -> None:
    """The store holds ciphertexts and the key opens them: either one in an output dir is a credential leak."""
    import shutil as sh

    lb = _load()
    root = tmp_path / "sandboxes" / "r1"
    _keyed_store(lb, root)
    live_plist = tmp_path / "live.plist"
    live_plist.write_bytes(plistlib.dumps({"EnvironmentVariables": {"AGENT_LB_FEDERATION_TOKEN": FAKE_TOKEN}}))
    monkeypatch.setattr(lb, "LIVE_PLIST", live_plist)
    secrets = lb.load_secrets(root)
    out = tmp_path / "out"
    out.mkdir()
    (out / "result.json").write_text("{}")
    sh.copy2(root / copied, out / Path(copied).name)
    exported = lb.scan_paths([out], secrets)
    assert (exported["found"], exported["hits"]) == (True, [str((out / Path(copied).name).resolve())])
    # In place, at their own paths, they are custody, not a leak; a hard link elsewhere in the root is a leak.
    in_place = lb.scan_paths([root], secrets, skip_exact=lb.custody_paths(root))
    assert (in_place["found"], in_place["complete"]) == (False, True)
    (root / "logs").mkdir()
    os.link(root / copied, root / "logs" / "primary.log")
    linked = lb.scan_paths([root], secrets, skip_exact=lb.custody_paths(root))
    assert linked["hits"] == [str((root / "logs" / "primary.log").resolve())]
    with pytest.raises(OSError):
        lb.export_logs(
            root, tmp_path / "export", [str((root / "logs" / "primary.log").resolve())], lb.Detector(secrets)
        )


def _record_start(lb: ModuleType, root: Path, key: bool = True):
    """What start records in the run's private dir for a mounted root it made: the mountpoint (MOUNT_IDS), the
    volume it attached and, once made, the store key. Returns the private dir (closed)."""
    priv = lb.make_private(root.name)
    priv.record("root", lb.identity(MOUNT_IDS[str(root)]))
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        priv.record("volume", {"st_dev": os.fstat(fd).st_dev, "device": lb._statfs_from(fd)})
    finally:
        os.close(fd)
    if key:
        priv.record("key", lb.identity((root / "data" / "encryption.key").stat()))
    priv.close()
    for fd, _ in lb._PINNED.values():  # start ran in another process: the command under test pins afresh
        os.close(fd)
    lb._PINNED.clear()
    return priv


def _teardown_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lb: ModuleType, root: Path) -> Path:
    """A sandbox as start leaves it: root (mounted_root) its own volume holding a keyed store, logs and a stamped
    sandbox.json, and the run's private dir recording the mountpoint, the volume and the key."""
    sandboxes = root.parent
    sandboxes.chmod(0o700)
    _keyed_store(lb, root)
    for sub in ("logs", "state", "home"):
        (root / sub).mkdir()
    (root / "logs" / "primary.log").write_text("INFO started\n")
    # Ports nothing listens on; labels that are never loaded: teardown has no live job to stop.
    meta = {"run_id": root.name, "ports": {"front": 2597, "primary": 2598}}
    (root / "sandbox.json").write_text(json.dumps(meta))
    live_plist = tmp_path / "live.plist"
    live_plist.write_bytes(plistlib.dumps({"EnvironmentVariables": {"AGENT_LB_FEDERATION_TOKEN": FAKE_TOKEN}}))
    monkeypatch.setattr(lb, "SANDBOXES", sandboxes.resolve())
    monkeypatch.setattr(lb, "LIVE_PLIST", live_plist)
    _record_start(lb, root)
    return root


@pytest.mark.skipif(sys.platform != "darwin", reason="teardown asks launchctl, which is macOS")
@pytest.mark.parametrize("planted", [None, "state/front.json", "home/.cache/blob", "logs/edge.jsonl"])
def test_teardown_scans_the_whole_root_and_refuses_export_on_a_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mounted_root: Path, planted: str | None
) -> None:
    """Integration over real files, ps, lsof and launchctl (for labels that were never loaded)."""
    lb = _load()
    root = _teardown_fixture(tmp_path, monkeypatch, lb, mounted_root)
    if planted:
        (root / planted).parent.mkdir(parents=True, exist_ok=True)
        (root / planted).write_text(f"x {MIRRORED_TOKEN} y")
    export = tmp_path / "export"
    result = lb.teardown(root.name, export)
    logs = result["logs"]
    if planted:
        assert (logs.get("scan_found"), logs.get("copied")) == (True, False), "a planted token must be found"
        assert logs.get("hits") == [planted]
        assert not export.exists()
    assert result["root_exists"] is False
    assert (logs.get("scan_scope"), logs.get("scan_complete")) == ("root", True)
    assert result.get("custody", {}).get("key_unlinked") is True
    if not planted:
        assert (logs["scan_found"], logs["copied"], logs["files_copied"]) == (False, True, 1)
        assert sorted(p.name for p in export.iterdir()) == ["primary.log"]


def _held_fetch(port: int, gzipped: bool = False) -> tuple[object, object]:
    """A stream:true Messages request; gzipped as agent-lb sends a body of 16 KB and up (a real Claude Code
    turn's), padded past that size."""
    import gzip
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    request = {"model": "claude-unit", "stream": True, "messages": []}
    if gzipped:
        request["system"] = "x" * (32 << 10)
    body = json.dumps(request).encode()
    headers = {"content-type": "application/json", **({"content-encoding": "gzip"} if gzipped else {})}
    conn.request("POST", "/v1/messages", body=gzip.compress(body) if gzipped else body, headers=headers)
    return conn, conn.getresponse()


@pytest.mark.parametrize("gzipped", [False, True])
@pytest.mark.parametrize("release", ["clear", "timeout"])
def test_edge_holds_a_stream_open_until_released(tmp_path: Path, release: str, gzipped: bool) -> None:
    """Integration over HTTP: the restart check's stream stays in flight until the check releases it.

    A hold that ends by its bound is reported as "timeout", which the check counts as a failed restart proof.
    gzipped (agent-lb-3 finding: the hold applied in 1 of 3 installed runs): agent-lb gzips a body of 16 KB and
    up on its way upstream, as a real Claude Code turn's is; read as plain JSON its stream flag was missed and
    the turn went upstream unheld.
    """
    import time

    lb = _load()
    fault_file = tmp_path / "fault-anthropic.json"
    bound = 30 if release == "clear" else 1
    fault_file.write_text(json.dumps({"nonce": "n1", "armed": True, "fault": "hold_stream", "n": bound}))
    rig = _EdgeRig(lb, fault_file, b"upstream must not be called", break_after=None)
    try:
        conn, resp = _held_fetch(rig.edge_port, gzipped)
        opening = b""
        while b"1, 2, 3" not in opening and (chunk := resp.read1(4096)):
            opening += chunk
        assert b"1, 2, 3" in opening, "the edge did not serve the held stream"
        if release == "clear":
            time.sleep(1.0)
            still_open = not rig.entries  # the edge logs a request when it ends
            fault_file.write_text(json.dumps({"nonce": "n2", "armed": False}))  # what `fault --clear` writes
        rest = resp.read()
        conn.close()
        time.sleep(0.2)
    finally:
        rig.close()
    if release == "clear":
        assert still_open, "the stream ended before the release"
    events = opening + rest
    assert b"message_start" in events and b"message_stop" in events and b"4, 5" in events
    assert b"upstream must not be called" not in events
    [entry] = rig.entries
    assert (entry["fault"], entry["status"], entry["released_by"]) == ("hold_stream", 200, release)
    assert entry["held_s"] >= (1.0 if release == "clear" else 0.9)


def test_edge_cuts_a_codex_stream_whatever_the_upstream_content_type(tmp_path: Path) -> None:
    """agent-lb-3 finding: the openai edge never applied an armed cut_after_bytes to Codex /codex/responses
    streams (3 of 3 installed runs: fault null, about 280 KB passed), because it took "streamed" from the upstream
    content-type. It is the request that streams: Codex's responses path always does, and agent-lb sends its
    large body gzipped. Integration over HTTP with an upstream that labels its stream application/octet-stream."""
    lb = _load()
    fault_file = tmp_path / "fault-openai.json"
    fault_file.write_text(json.dumps({"nonce": "n1", "armed": True, "fault": "cut_after_bytes", "n": 2048}))
    rig = _EdgeRig(lb, fault_file, SSE_BODY, break_after=None, edge="openai", content_type=None)
    request = json.dumps({"model": "gpt-unit", "stream": True, "input": "x" * (32 << 10)}).encode()
    try:
        received, clean = rig.fetch(0.0, path="/codex/responses", body=request, gzipped=True)
    finally:
        rig.close()
    assert (len(received), clean) == (2048, False), "the armed cut was not applied to the Codex stream"
    assert [(e["fault"], e["bytes"]) for e in rig.entries] == [("cut_after_bytes:2048", 2048)]


def test_hold_stream_fault_is_bounded() -> None:
    lb = _load()
    assert lb.parse_fault("hold_stream:30") == ("hold_stream", 30)
    for spec in ("hold_stream:0", f"hold_stream:{lb.HOLD_MAX_S + 1}", "hold_stream:", "hold_stream:-1"):
        with pytest.raises(lb.Refused):
            lb.parse_fault(spec)


# ---------------------------------------------------------------- fix round 2 (review FAIL on 41a2f667)
# Each test below replays one review finding and fails on 41a2f667 for the reason named in it.


def test_log_export_refuses_a_directory_swapped_for_a_link_after_the_scan(tmp_path: Path) -> None:
    """Finding: no-follow covered only the last component. The scan reads a clean logs/d/encryption.key;
    then logs/d becomes a link to <root>/data, and export must not copy the real key out."""
    lb = _load()
    root = tmp_path / "sandboxes" / "r1"
    _key(root)
    (root / "data" / "encryption.key").write_bytes(b"real-store-key-" + b"q" * 29)
    (root / "logs" / "d").mkdir(parents=True)
    (root / "logs" / "d" / "encryption.key").write_text("an innocent log line\n")
    (root / "logs" / "primary.log").write_text("INFO started\n")
    scanned = lb.scan_paths([root / "logs"], [FAKE_TOKEN])
    assert (scanned["found"], scanned["complete"], scanned["files_scanned"]) == (False, True, 2)
    (root / "logs" / "d" / "encryption.key").unlink()
    (root / "logs" / "d").rmdir()
    (root / "logs" / "d").symlink_to(root / "data", target_is_directory=True)
    export = tmp_path / "export"
    with pytest.raises(OSError):
        lb.export_logs(root, export, sorted(scanned["_files"]), lb.Detector([FAKE_TOKEN]))
    copied = [p.read_bytes() for p in export.rglob("*") if p.is_file()]
    assert all(b"real-store-key" not in data for data in copied), "the store key left the root"


def test_log_export_scans_the_bytes_it_copies(tmp_path: Path) -> None:
    """Finding: the exported bytes were never scanned. A log that gains a token after the scan is not exported."""
    lb = _load()
    root = tmp_path / "sandboxes" / "r1"
    (root / "logs").mkdir(parents=True)
    (root / "logs" / "edge.jsonl").write_text('{"ok": 1}\n')
    scanned = lb.scan_paths([root / "logs"], [FAKE_TOKEN])
    assert scanned["found"] is False
    with (root / "logs" / "edge.jsonl").open("a") as fh:
        fh.write(f"Authorization: Bearer {FAKE_TOKEN}\n")
    export = tmp_path / "export"
    with pytest.raises(lb.LeakFound):
        lb.export_logs(root, export, scanned["_files"], lb.Detector([FAKE_TOKEN]))
    assert not any(FAKE_TOKEN.encode() in p.read_bytes() for p in export.rglob("*") if p.is_file())


def test_a_store_snapshot_from_before_a_mirror_cycle_is_still_a_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding: a DB copy taken before the mirror re-encrypted the rows matched no current ciphertext.

    Any ciphertext the store key opens is a hit, whole or as a fragment (SQLite splits long blobs across
    overflow pages). The control: the same scan without the key misses it, as 41a2f667 did.
    """
    import shutil as sh
    import sqlite3

    from cryptography.fernet import Fernet

    lb = _load()
    root = tmp_path / "sandboxes" / "r1"
    _keyed_store(lb, root)
    live_plist = tmp_path / "live.plist"
    live_plist.write_bytes(plistlib.dumps({"EnvironmentVariables": {"AGENT_LB_FEDERATION_TOKEN": FAKE_TOKEN}}))
    monkeypatch.setattr(lb, "LIVE_PLIST", live_plist)
    out = tmp_path / "out"
    out.mkdir()
    sh.copy2(root / "data" / "store.db", out / "snapshot.db")
    fernet = Fernet((root / "data" / "encryption.key").read_bytes())
    con = sqlite3.connect(root / "data" / "store.db")
    old = con.execute("SELECT access_token_encrypted FROM accounts").fetchone()[0]
    con.execute(
        "UPDATE accounts SET access_token_encrypted = ?, refresh_token_encrypted = ?",
        (fernet.encrypt(MIRRORED_TOKEN.encode()), fernet.encrypt(b"")),
    )
    con.commit()
    con.close()
    (out / "fragment.txt").write_bytes(b"page tail " + bytes(old)[:60] + b" next page")
    secrets = lb.load_secrets(root)
    key = lb.load_key(root)
    keyed = lb.scan_paths([out], secrets, keys=[key])
    unkeyed = lb.scan_paths([out], secrets)
    assert sorted(Path(h).name for h in keyed["hits"]) == ["fragment.txt", "snapshot.db"]
    assert unkeyed["found"] is False  # current ciphertexts and plaintexts alone miss the snapshot
    # Ciphertexts of another key (the live store's) are not this sandbox's leak.
    other = Fernet(Fernet.generate_key()).encrypt(MIRRORED_TOKEN.encode())
    (out / "snapshot.db").unlink()
    (out / "fragment.txt").write_bytes(other)
    assert lb.scan_paths([out], secrets, keys=[key])["found"] is False


@pytest.mark.parametrize(
    "encode",
    [
        base64.b64encode,
        base64.urlsafe_b64encode,
        base64.encodebytes,  # MIME: a newline every 76 characters
        lambda raw: b"\r\n".join(
            base64.urlsafe_b64encode(raw)[i : i + 64] for i in range(0, len(base64.urlsafe_b64encode(raw)), 64)
        ),
    ],
    ids=["standard", "urlsafe", "mime-wrapped", "urlsafe-crlf-64"],
)
def test_a_base64_encoded_store_snapshot_from_before_a_mirror_cycle_is_still_a_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, encode
) -> None:
    """Review finding on 54018fbe: the snapshot above, base64-encoded, read clean (found False, 10924 bytes
    exported). The key must open its ciphertexts inside base64 too, in either alphabet, wrapped or not, and
    across any chunk boundary of the streaming scan that export uses. The controls: without the key it is
    missed, and the same encoding of another key's store is not this sandbox's leak."""
    import io
    import shutil as sh
    import sqlite3

    from cryptography.fernet import Fernet

    lb = _load()
    root = tmp_path / "sandboxes" / "r1"
    _keyed_store(lb, root)
    live_plist = tmp_path / "live.plist"
    live_plist.write_bytes(plistlib.dumps({"EnvironmentVariables": {"AGENT_LB_FEDERATION_TOKEN": FAKE_TOKEN}}))
    monkeypatch.setattr(lb, "LIVE_PLIST", live_plist)
    out = tmp_path / "out"
    out.mkdir()
    snapshot = (root / "data" / "store.db").read_bytes()
    fernet = Fernet((root / "data" / "encryption.key").read_bytes())
    con = sqlite3.connect(root / "data" / "store.db")
    con.execute(
        "UPDATE accounts SET access_token_encrypted = ?, refresh_token_encrypted = ?",
        (fernet.encrypt(MIRRORED_TOKEN.encode()), fernet.encrypt(b"")),
    )
    con.commit()
    con.close()
    encoded = encode(snapshot)
    (out / "snapshot.b64").write_bytes(encoded)
    secrets = lb.load_secrets(root)
    key = lb.load_key(root)
    assert lb.scan_paths([out], secrets, keys=[key])["hits"] == [str((out / "snapshot.b64").resolve())]
    assert lb.scan_paths([out], secrets)["found"] is False  # current ciphertexts and plaintexts alone miss it
    detector = lb.Detector(secrets, [key])
    for chunk in (61, 97, 128):  # boundaries at every alignment of the encoded text
        assert lb.scan_stream(io.BytesIO(encoded).read, detector, chunk=chunk)[0] is True, chunk
    other_root = tmp_path / "other" / "r2"
    _keyed_store(lb, other_root)
    (out / "snapshot.b64").write_bytes(encode((other_root / "data" / "store.db").read_bytes()))
    sh.rmtree(other_root)
    assert lb.scan_paths([out], secrets, keys=[key])["found"] is False


DESCENDANT_PARENT = (
    "import subprocess, sys, time\n"
    "token = sys.stdin.readline().strip()\n"
    "if sys.argv[1] == 'token':\n"
    "    env = {'PATH': '/usr/bin:/bin', 'AGENT_LB_FEDERATION_TOKEN': token}\n"
    "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'], env=env)\n"
    "else:\n"
    "    child = subprocess.Popen(['/bin/sleep', '20'])  # a platform binary: ps never shows its environment\n"
    "print(child.pid, flush=True)\n"
    "time.sleep(20)\n"
)


@pytest.mark.skipif(sys.platform != "darwin", reason="ps -E semantics are macOS")
@pytest.mark.parametrize("child", ["token", "hidden"])
def test_process_scan_reads_children_whose_argv_names_neither_root_nor_run(tmp_path: Path, child: str) -> None:
    """Review finding: a sandbox child whose argv omits the run id and root was never selected, so a token in
    its environment read clean (process_envs_requested [111], found False, complete True). Every descendant of
    a run process is scanned; one whose environment ps cannot show leaves the scan incomplete."""
    import signal
    import time

    lb = _load()
    root = (tmp_path / "sandboxes" / "r1").resolve()
    root.mkdir(parents=True)
    parent = subprocess.Popen(
        [sys.executable, "-c", DESCENDANT_PARENT, child, str(root)],
        env={"PATH": "/usr/bin:/bin", "LB_SANDBOX_ROOT": str(root)},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        start_new_session=True,  # parent and child in one group, killed together below
    )
    try:
        parent.stdin.write(FAKE_TOKEN.encode() + b"\n")
        parent.stdin.close()
        child_pid = int(parent.stdout.readline())
        time.sleep(0.5)
        result = lb.scan_processes(root, "r1", [FAKE_TOKEN])
    finally:
        try:
            os.killpg(parent.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        parent.wait(10)
    assert parent.pid in result["visible_pids"]
    if child == "token":
        assert (result["found"], result["hits"], result["descendant_pids"]) == (True, [child_pid], [child_pid])
    else:
        assert (result["found"], result["complete"], result["invisible_pids"]) == (False, False, [child_pid])


@pytest.mark.skipif(sys.platform != "darwin", reason="ps -E semantics are macOS")
def test_process_scan_is_incomplete_when_any_run_process_hides_its_environment(tmp_path: Path) -> None:
    """Finding: environment visibility was checked only for named roles. A run process whose environment ps
    does not show as this run's (no LB_SANDBOX_ROOT entry after its argv) makes the scan incomplete."""
    import time

    lb = _load()
    root = (tmp_path / "sandboxes" / "r1").resolve()
    root.mkdir(parents=True)
    seen = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(20)", str(root)],
        env={"PATH": "/usr/bin:/bin", "LB_SANDBOX_ROOT": str(root)},
    )
    # The marker only in argv, not the environment: it must not count as a shown environment.
    unseen = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(20)", str(root), f"LB_SANDBOX_ROOT={root}"],
        env={"PATH": "/usr/bin:/bin"},
    )
    try:
        time.sleep(0.5)
        result = lb.scan_processes(root, "r1", [FAKE_TOKEN])
    finally:
        for proc in (seen, unseen):
            proc.kill()
            proc.wait()
    assert seen.pid in result["visible_pids"] and unseen.pid not in result["visible_pids"]
    assert (result["found"], result["complete"], result["invisible_pids"]) == (False, False, [unseen.pid])


@pytest.mark.skipif(sys.platform != "darwin", reason="ps -E semantics are macOS")
def test_process_scan_skips_a_label_only_launchctl_call_and_still_requires_run_id_commands(tmp_path: Path) -> None:
    """Pre-land check, round 3: lb-restart's `launchctl kickstart -k gui/<uid>/<label>` waits out the drain and
    macOS never shows a platform binary's environment, so naming the label made the cutover scan incomplete.
    A command naming only the label is not selected; one passing --run-id must still show its environment."""
    import time

    lb = _load()
    root = (tmp_path / "sandboxes" / "r1").resolve()
    root.mkdir(parents=True)
    label = lb.labels_for("r1")[0]
    sleep = [sys.executable, "-c", "import time; time.sleep(20)"]
    label_only = subprocess.Popen([*sleep, "kickstart", "-k", f"gui/{os.getuid()}/{label}"], env={"PATH": "/bin"})
    by_run_id = subprocess.Popen([*sleep, "--run-id", "r1"], env={"PATH": "/bin"})
    try:
        time.sleep(0.5)
        result = lb.scan_processes(root, "r1", [FAKE_TOKEN])
    finally:
        for proc in (label_only, by_run_id):
            proc.kill()
            proc.wait()
    assert label_only.pid not in result["pids"] + result["invisible_pids"]
    assert (result["complete"], result["invisible_pids"]) == (False, [by_run_id.pid])


@pytest.mark.skipif(sys.platform != "darwin", reason="teardown asks launchctl, which is macOS")
def test_teardown_stops_a_process_that_names_the_run_label_and_never_reports_its_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mounted_root: Path
) -> None:
    """Findings: a leftover naming the run's label but not the root survived, and its raw command (which can
    hold a token) went into the JSON. It is stopped, leftovers are reported by pid and hash only, and a process
    that holds the run id only as a bare word (as the live agent-lb holds `aneyman`) is never signalled."""
    lb = _load()
    root = _teardown_fixture(tmp_path, monkeypatch, lb, mounted_root)
    sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]
    labelled = subprocess.Popen([*sleeper, f"gui/{os.getuid()}/com.agent-lb.drill.sbx-{root.name}", FAKE_TOKEN])
    bare = subprocess.Popen([*sleeper, f"/x/{root.name}/y", root.name, FAKE_TOKEN])
    try:
        result = lb.teardown(root.name, None)
        stopped = labelled.wait(10) is not None
        spared = bare.poll() is None
    finally:
        for proc in (labelled, bare):
            if proc.poll() is None:
                proc.kill()
            proc.wait()
    assert stopped and spared and result["processes"] == []
    assert FAKE_TOKEN not in json.dumps(result)
    assert set(lb.command_ref("x " + FAKE_TOKEN)) <= set("0123456789abcdef")


@pytest.mark.skipif(sys.platform != "darwin", reason="teardown asks launchctl, which is macOS")
@pytest.mark.parametrize("meta", ["absent", "symlink", "hardlink", "other-run"])
def test_stop_signals_scans_and_deletes_nothing_without_this_runs_stamped_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mounted_root: Path, meta: str
) -> None:
    """Findings: stop killed any process naming the run id with no check that the sandbox existed, and read
    sandbox.json through a link to live state/front.json. Without a single-link sandbox.json stamped with this
    run id, nothing is signalled, the root is not scanned or deleted, its key stays, and stop exits 2."""
    import argparse

    lb = _load()
    root = _teardown_fixture(tmp_path, monkeypatch, lb, mounted_root)
    monkeypatch.setattr(lb, "check_launchctl", lambda: None)
    monkeypatch.setattr(lb.signal, "signal", lambda *args: None)  # stop ignores SIGINT; not in the test runner
    live = tmp_path / "live-state" / "front.json"
    live.parent.mkdir()
    live.write_text(json.dumps({"run_id": root.name, "preferred": 2457}))
    (root / "sandbox.json").unlink()
    if meta == "symlink":
        (root / "sandbox.json").symlink_to(live)
    elif meta == "hardlink":
        # The run's own volume: no hard link to a live file can exist there, so a second name in the root stands in.
        (root / "state" / "stamped.json").write_text(live.read_text())
        os.link(root / "state" / "stamped.json", root / "sandbox.json")
    elif meta == "other-run":
        (root / "sandbox.json").write_text(json.dumps({"run_id": "lbsbx-someone-else", "ports": {}}))
    label = f"com.agent-lb.drill.sbx-{root.name}"
    victim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", str(root / "runtime"), label])
    try:
        code = lb.cmd_stop(argparse.Namespace(run_id=root.name, logs_to=None))
        spared = victim.poll() is None
    finally:
        victim.kill()
        victim.wait()
    assert spared, "stop signalled a process for a run id with no stamped sandbox.json"
    assert code == lb.EXIT_REFUSED
    assert (root / "data" / "encryption.key").exists() and (root / "logs" / "primary.log").exists()
    assert json.loads(live.read_text()) == {"run_id": root.name, "preferred": 2457}


def test_scan_under_the_root_never_reads_a_second_hard_link(tmp_path: Path) -> None:
    """Finding: a hard link to the live store planted at logs/live.db was read by the root scan. Under the
    root a file with a second link is not opened for reading: the scan is incomplete, never quietly clean."""
    lb = _load()
    root = tmp_path / "sandboxes" / "r1"
    (root / "logs").mkdir(parents=True)
    (root / "logs" / "own.log").write_text("clean\n")
    live = tmp_path / "live.db"
    live.write_text(f"live store {FAKE_TOKEN}")
    os.link(live, root / "logs" / "live.db")
    result = lb.scan_paths([root], [FAKE_TOKEN], root=root)
    planted = os.path.join(os.path.realpath(root), "logs", "live.db")
    assert (result["complete"], result["files_scanned"], result["hits"]) == (False, 1, [])
    assert result["unreadable_paths"] == [planted]
    elsewhere = lb.scan_paths([root], [FAKE_TOKEN])  # control: outside a root's walk the same file is a hit
    assert elsewhere["hits"] == [planted]


def test_a_volume_event_log_the_run_could_write_is_still_scanned(tmp_path: Path) -> None:
    """The root-owned .fseventsd macOS makes on each mounted root is passed over; a .fseventsd the run could
    have made (owned by this user, or below the top of the root) is scanned like any other directory."""
    lb = _load()
    root = tmp_path / "sandboxes" / "r1"
    for planted in (root / ".fseventsd", root / "logs" / ".fseventsd"):
        planted.mkdir(parents=True)
        (planted / "0000").write_text(FAKE_TOKEN)
    result = lb.scan_paths([root], [FAKE_TOKEN], root=root)
    base = os.path.realpath(root)
    assert result["complete"] is True
    assert sorted(result["hits"]) == [
        os.path.join(base, ".fseventsd", "0000"),
        os.path.join(base, "logs", ".fseventsd", "0000"),
    ]


@pytest.mark.skipif(sys.platform != "darwin", reason="hdiutil is macOS")
def test_another_volumes_event_log_the_run_could_write_is_still_scanned(tmp_path: Path, mounted_root: Path) -> None:
    """The shared-tree scan of ~/.agent-lb enters a concurrent run's root, its own volume, whose root-owned
    .fseventsd it cannot read (the S1-3 check on the installed copy went incomplete on exactly that). The skip
    covers the top of any volume, root-owned only: a .fseventsd this user made there is scanned like any other."""
    lb = _load()
    planted = mounted_root / ".fseventsd" / "0000"
    planted.parent.mkdir()
    planted.write_text(FAKE_TOKEN)
    top = os.open(mounted_root, os.O_RDONLY | os.O_DIRECTORY)
    parent = os.open(mounted_root.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert (lb.volume_top(top), lb.volume_top(parent)) == (True, False)
    finally:
        os.close(top)
        os.close(parent)
    result = lb.scan_paths([mounted_root.parent], [FAKE_TOKEN])
    assert result["hits"] == [os.path.join(os.path.realpath(mounted_root), ".fseventsd", "0000")]


def test_scan_never_follows_a_directory_swapped_for_a_link_mid_walk(tmp_path: Path) -> None:
    """Finding: O_NOFOLLOW covered only the final component, so a parent directory replaced by a link after
    the walk listed it led the scanner out of the tree. Each entry is opened from its parent's descriptor."""
    import shutil as sh

    lb = _load()
    top = tmp_path / "logs"
    (top / "sub").mkdir(parents=True)
    (top / "a.log").write_text("clean\n")
    (top / "sub" / "b.log").write_text("clean\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "b.log").write_text(FAKE_TOKEN)
    unreadable: list[str] = []
    walk = lb.walk_files([top], None, [], unreadable)
    first, fd = next(walk)  # the walk has listed logs/ and is about to descend into sub/
    os.close(fd)
    sh.rmtree(top / "sub")
    (top / "sub").symlink_to(outside, target_is_directory=True)
    rest = list(walk)
    for _, fd in rest:
        os.close(fd)
    assert Path(first).name == "a.log"
    assert [path for path, _ in rest] == []
    assert unreadable == [os.path.join(os.path.realpath(top), "sub")]


def _home_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lb: ModuleType) -> tuple[Path, Path]:
    """A home with a live agent-lb (state/front.json, the store key) and a sandbox root under it."""
    home = tmp_path / "home"
    lb_home = home / ".agent-lb"
    live_state = lb_home / "state"
    live_state.mkdir(parents=True)
    (live_state / "front.json").write_text('{"preferred": 2457}')
    (lb_home / "encryption.key").write_bytes(b"live key")
    for name, value in {
        "REAL_HOME": home,
        "LB_HOME": lb_home,
        "SANDBOXES": lb_home / "sandboxes",
        "LIVE_RUNTIME": lb_home / "runtime" / "agent-lb",
        "LIVE_KEY_DIR_LEGACY": home / ".codex-lb",
        "LIVE_PLIST": home / "Library" / "LaunchAgents" / "com.aneyman.agent-lb.plist",
    }.items():
        monkeypatch.setattr(lb, name, value)
    root = lb_home / "sandboxes" / "r1"
    for sub in ("state", "bin", "data"):
        (root / sub).mkdir(parents=True)
    return root, lb_home


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt is macOS")
@pytest.mark.parametrize("reads", [True, False])
@pytest.mark.parametrize("planted", ["state/front.json.tmp", "bin/lb-sandbox"])
def test_a_confined_writer_never_writes_through_a_link_planted_in_the_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reads: bool, planted: str
) -> None:
    """Findings: the front's writeFileSync(state/front.json.tmp) and start's copy into bin/lb-sandbox open by
    name and follow a link planted in the root to live state/front.json. Under the root's Seatbelt profile
    (the aux and the front it starts run under it; start's copy runs in a confined child) the write is denied.
    """
    lb = _load()
    root, lb_home = _home_layout(tmp_path, monkeypatch, lb)
    live = lb_home / "state" / "front.json"
    (root / planted).symlink_to(live)

    def write_by_name() -> None:
        with open(root / planted, "w") as fh:  # what writeFileSync and shutil.copy2 do: open, follow, truncate
            fh.write("{}")

    with pytest.raises((lb.Refused, lb.Unhealthy)):
        lb.run_confined(root, write_by_name, reads=reads)
    assert live.read_text() == '{"preferred": 2457}'
    lb.run_confined(root, lambda: (root / "state" / "own.json").write_text("{}"), reads=reads)  # control
    assert (root / "state" / "own.json").read_text() == "{}"


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt is macOS")
def test_store_reads_never_reach_live_custody_through_a_swapped_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding: after the custody checks, data/ swapped for a link to the live agent-lb home let SQLite (in the
    app, and in read_accounts) open the live store and key by name. In the root's profile that open is denied."""
    import shutil as sh
    import sqlite3

    lb = _load()
    root, lb_home = _home_layout(tmp_path, monkeypatch, lb)
    for directory in (lb_home, root / "data"):
        con = sqlite3.connect(directory / "store.db")
        con.execute(
            "CREATE TABLE accounts (id, provider, status, access_expires_at, access_token_encrypted,"
            " id_token_encrypted, reset_at, blocked_at, refresh_token_encrypted)"
        )
        con.execute("INSERT INTO accounts (id, provider, status) VALUES (?, 'anthropic', 'active')", (directory.name,))
        con.commit()
        con.close()
    rows = lb.run_confined(root, lambda: lb.store_rows(root), reads=True)
    assert [row[0] for row in rows] == ["data"]  # control: the sandbox's own store reads
    sh.rmtree(root / "data")
    (root / "data").symlink_to(lb_home, target_is_directory=True)
    with pytest.raises((lb.Refused, lb.Unhealthy)):
        lb.run_confined(root, lambda: lb.store_rows(root), reads=True)
    with pytest.raises((lb.Refused, lb.Unhealthy)):
        lb.run_confined(root, lambda: (root / "data" / "encryption.key").read_bytes(), reads=True)


def test_serve_refuses_a_root_that_is_not_its_own_volume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A root on the home volume can hold a hard link to a live file; _serve refuses it before the token."""
    lb = _load()
    sandboxes = tmp_path / "sandboxes"
    root = sandboxes / "r1"
    _key(root)
    (root / "sandbox.json").write_text(json.dumps({"run_id": "r1", "ports": SERVE_PORTS}))
    monkeypatch.setattr(lb, "SANDBOXES", sandboxes)
    monkeypatch.setattr(lb, "live_federation_token", lambda plist=None: pytest.fail("read the token"))
    monkeypatch.setenv("LB_SANDBOX_ROOT", str(root.resolve()))
    with pytest.raises(lb.Refused):
        lb.cmd_serve(str(root), ["--host", "127.0.0.1", "--port", "2481"])


@pytest.mark.skipif(sys.platform != "darwin", reason="teardown asks launchctl, which is macOS")
def test_teardown_never_unlinks_a_key_through_a_linked_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mounted_root: Path
) -> None:
    """Finding: with <root>/data swapped for a link to the live data dir, stop deleted the live key."""
    import shutil as sh

    lb = _load()
    root = _teardown_fixture(tmp_path, monkeypatch, lb, mounted_root)
    live = tmp_path / "live-data"
    live.mkdir()
    (live / "encryption.key").write_bytes(b"live key")
    sh.rmtree(root / "data")
    (root / "data").symlink_to(live, target_is_directory=True)
    result = lb.teardown(root.name, None)
    assert (live / "encryption.key").read_bytes() == b"live key"
    assert result["custody"]["key_unlinked"] is False
    assert result["root_exists"] is False


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_restart_log_never_writes_through_a_planted_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Finding: logs/lb-restart.log was opened (append, following links) before lb-restart's config guard."""
    import argparse

    lb = _load()
    sandboxes = tmp_path / "sandboxes"
    root = sandboxes / "r1"
    (root / "logs").mkdir(parents=True)
    (root / "sandbox.json").write_text(json.dumps({"run_id": "r1", "ports": SERVE_PORTS}))
    live = tmp_path / "live.plist"
    live.write_text("live plist")
    if kind == "symlink":
        (root / "logs" / "lb-restart.log").symlink_to(live)
    else:
        os.link(live, root / "logs" / "lb-restart.log")
    sandboxes.chmod(0o700)  # as start makes it, whatever the umask
    monkeypatch.setattr(lb, "SANDBOXES", sandboxes)
    monkeypatch.setattr(lb, "check_launchctl", lambda: None)
    # The root's own volume is checked first now; stubbed so the link refusal itself stays under test.
    monkeypatch.setattr(lb, "check_own_volume", lambda root: None)
    with pytest.raises(lb.Refused):
        lb.cmd_restart(argparse.Namespace(run_id="r1", reason="unit"))
    assert live.read_text() == "live plist"


@pytest.mark.skipif(sys.platform != "darwin", reason="hdiutil is macOS")
@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_client_env_never_truncates_a_planted_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest, kind: str
) -> None:
    """Finding: client-env truncated clients/codex/config.toml through a planted link to a live file. On the run's
    own volume a symlink is replaced by a new inode, never followed; a hard link to a live file cannot be made
    there (another volume), and a root that is not its own volume is refused before anything is written."""
    import argparse

    lb = _load()
    live = tmp_path / "live-config.toml"
    live.write_text("live config")
    if kind == "symlink":
        root = request.getfixturevalue("mounted_root")
    else:
        root = tmp_path / "sandboxes" / "r1"
        root.mkdir(parents=True)
    sandboxes = root.parent
    (root / "clients" / "codex").mkdir(parents=True)
    sandboxes.chmod(0o700)  # as start makes it, whatever the umask
    (root / "sandbox.json").write_text(
        json.dumps({"run_id": "r1", "ports": SERVE_PORTS, "label": "com.agent-lb.drill.sbx-r1"})
    )
    monkeypatch.setattr(lb, "SANDBOXES", sandboxes)
    args = argparse.Namespace(run_id="r1", vendor="codex")
    if kind == "symlink":
        (root / "clients" / "codex" / "config.toml").symlink_to(live)
        lb.cmd_client_env(args)
        assert "backend-api/codex" in (root / "clients" / "codex" / "config.toml").read_text()
    else:
        os.link(live, root / "clients" / "codex" / "config.toml")
        with pytest.raises(lb.Refused):
            lb.cmd_client_env(args)
    assert live.read_text() == "live config"


@pytest.mark.parametrize("name", ["store.db", "encryption.key", "store.db-wal"])
def test_serve_refuses_a_store_or_key_hard_linked_to_live_custody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Finding: a hard link passed the symlink and resolve checks, so the sandbox opened the live DB."""
    lb = _load()
    sandboxes = tmp_path / "sandboxes"
    root = sandboxes / "r1"
    _key(root)
    (root / "sandbox.json").write_text(json.dumps({"run_id": "r1", "ports": SERVE_PORTS}))
    live = tmp_path / "live-data"
    live.mkdir()
    (live / name).write_bytes(b"live custody file")
    (root / "data" / name).unlink(missing_ok=True)
    os.link(live / name, root / "data" / name)
    live_plist = tmp_path / "live.plist"
    live_plist.write_bytes(plistlib.dumps({"EnvironmentVariables": {"AGENT_LB_FEDERATION_TOKEN": FAKE_TOKEN}}))
    monkeypatch.setattr(lb, "SANDBOXES", sandboxes)
    monkeypatch.setattr(lb, "LIVE_PLIST", live_plist)
    monkeypatch.setenv("LB_SANDBOX_ROOT", str(root.resolve()))
    _install_venv(tmp_path)
    _use_bootstrap(lb, tmp_path)
    with pytest.raises(lb.Refused):
        lb.serve_exec_args(root, ["--host", "127.0.0.1", "--port", "2482"])
    (root / "data" / name).unlink()
    if name == "encryption.key":
        _key(root)
    assert lb.serve_exec_args(root, ["--host", "127.0.0.1", "--port", "2482"])[3] == FAKE_TOKEN  # control


# ---------------------------------------------------------------- S2-2 fix round 4 (live-safety review, cf915e10)


def _live_home_with_linked_sandboxes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lb: ModuleType, where: str):
    """A home whose live agent-lb dir holds state the sandbox code must never touch, reached through a link.

    where="sandboxes": ~/.agent-lb/sandboxes is a link to ~/.agent-lb, so run id `state` names the live state dir.
    where="ancestor": ~/.agent-lb itself is a link (to the real agent-lb home) and run r1's root sits under it.
    The target holds a sandbox.json stamped with the run id, the worst case: a stamp alone must not be enough.
    Returns (run_id, target dir, its files before)."""
    home = tmp_path / "home"
    if where == "sandboxes":
        lb_home = home / ".agent-lb"
        lb_home.mkdir(parents=True)
        (lb_home / "sandboxes").symlink_to(lb_home, target_is_directory=True)
        run_id, target = "state", lb_home / "state"
    else:
        real = tmp_path / "real-agent-lb"
        (real / "sandboxes").mkdir(parents=True)
        (real / "sandboxes").chmod(0o700)
        home.mkdir()
        (home / ".agent-lb").symlink_to(real, target_is_directory=True)
        lb_home = home / ".agent-lb"
        run_id, target = "r1", real / "sandboxes" / "r1"
    (target / "data").mkdir(parents=True)
    (target / "front.json").write_text('{"preferred": 2457}')
    (target / "data" / "encryption.key").write_bytes(b"live key")
    (target / "sandbox.json").write_text(json.dumps({"run_id": run_id, "ports": SERVE_PORTS}))
    monkeypatch.setattr(lb, "SANDBOXES", lb_home / "sandboxes")
    monkeypatch.setattr(lb, "IMAGES", lb_home / "sandboxes" / ".images", raising=False)
    live_plist = tmp_path / "live.plist"  # a regressed teardown loads secrets: never the real live plist
    live_plist.write_bytes(plistlib.dumps({"EnvironmentVariables": {"AGENT_LB_FEDERATION_TOKEN": FAKE_TOKEN}}))
    monkeypatch.setattr(lb, "LIVE_PLIST", live_plist)
    files = {p: p.read_bytes() for p in target.rglob("*") if p.is_file()}
    return run_id, target, files


@pytest.mark.parametrize("where", ["sandboxes", "ancestor"])
def test_a_linked_sandboxes_dir_never_makes_live_state_a_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    """M3 (lb-sandbox:166,213,2253): with ~/.agent-lb/sandboxes a link to ~/.agent-lb, check_root resolved both
    sides and accepted live state as root `state`; stop then removed it recursively, and restart wrote its log
    there first. Every owner command now refuses a linked sandboxes dir or ancestor before touching anything."""
    import argparse

    lb = _load()
    run_id, target, files = _live_home_with_linked_sandboxes(tmp_path, monkeypatch, lb, where)
    with pytest.raises(lb.Refused):
        lb.check_root(lb.SANDBOXES / run_id)
    with pytest.raises(lb.Refused):
        lb.load_meta(run_id)
    monkeypatch.setattr(lb, "check_launchctl", lambda: None)
    with pytest.raises(lb.Refused):
        lb.cmd_restart(argparse.Namespace(run_id=run_id, reason="unit"))
    if run_id in lb.RESERVED_RUN_IDS:  # since S1-3 a run id naming a live directory is refused outright
        with pytest.raises(lb.Refused):
            lb.teardown(run_id, None)
    else:
        result = lb.teardown(run_id, None)
        assert result.get("refused") and result["clean"] is False
    assert {p: p.read_bytes() for p in target.rglob("*") if p.is_file()} == files
    assert not (target / "logs").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="hdiutil is macOS")
def test_teardown_removes_the_root_inside_the_sandboxes_dir_it_pinned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mounted_root: Path
) -> None:
    """M3, during teardown: the sandboxes dir is swapped for a link to ~/.agent-lb after teardown checked it,
    with ~/.agent-lb/r1 standing for live state. The root is removed by name inside the descriptor teardown
    pinned (by its recorded identity, through the run's private dir), so the real sandbox goes and the live dir
    stays. check_root is stubbed to isolate the pin (it would refuse the swap first); launchctl and ps are the OS
    edge; the volume detaches for real before the swap."""
    lb = _load()
    root = mounted_root
    sandboxes = root.parent
    lb_home = tmp_path / "home" / ".agent-lb"
    (root / "logs").mkdir()
    sandboxes.chmod(0o700)
    (root / "sandbox.json").write_text(json.dumps({"run_id": "r1", "ports": {}}))
    live = lb_home / "r1"
    live.mkdir(parents=True)
    (live / "front.json").write_text('{"preferred": 2457}')
    monkeypatch.setattr(lb, "SANDBOXES", sandboxes)
    monkeypatch.setattr(lb, "LIVE_PLIST", tmp_path / "no-live.plist")
    monkeypatch.setattr(lb, "label_loaded", lambda label: False)
    monkeypatch.setattr(lb, "processes", lambda: [])
    monkeypatch.setattr(lb, "load_secrets", lambda root: [FAKE_TOKEN])
    monkeypatch.setattr(lb, "load_key", lambda root: None)
    _record_start(lb, root, key=False)
    real_detach = lb.detach_volume

    def detach_then_swap(path: Path) -> dict:
        done = real_detach(path)
        sandboxes.rename(tmp_path / "moved")
        sandboxes.symlink_to(lb_home, target_is_directory=True)
        return done

    monkeypatch.setattr(lb, "detach_volume", detach_then_swap)
    monkeypatch.setattr(lb, "check_root", lambda path, sandboxes=None: path)
    result = lb.teardown("r1", None)
    assert (live / "front.json").read_text() == '{"preferred": 2457}'
    assert result["volume"]["root_removed"] is True and not (tmp_path / "moved" / "r1").exists()
    assert sorted(os.listdir(tmp_path / "moved")) == [], "the private dir and its record go with the root"


@pytest.mark.parametrize(
    "shim", ["link-into-root", "bin-dir-link", "group-writable", "private-script", "native-elsewhere"]
)
def test_serve_execs_only_the_installed_venv_python(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shim: str) -> None:
    """M1 on lb-sandbox's side: _serve exec'd <root>/runtime/.venv/bin/python, a name anything in the root can
    swap for a shim, and handed it the token pipe. It now execs the installed venv's python, and what that name
    leads to is checked just before the exec. The plist start writes names the same interpreter.

    S2-3 (M3, lb-sandbox:266): a private same-user 0755 shell script outside the sandboxes passed the shape
    checks. Identity now: a native executable in the dir the venv's pyvenv.cfg names."""
    lb = _load()
    sandboxes = tmp_path / "sandboxes"
    root = sandboxes / "r1"
    _key(root)
    (root / "sandbox.json").write_text(json.dumps({"run_id": "r1", "ports": SERVE_PORTS}))
    live_plist = tmp_path / "live.plist"
    live_plist.write_bytes(plistlib.dumps({"EnvironmentVariables": {"AGENT_LB_FEDERATION_TOKEN": FAKE_TOKEN}}))
    monkeypatch.setattr(lb, "SANDBOXES", sandboxes)
    monkeypatch.setattr(lb, "LIVE_PLIST", live_plist)
    monkeypatch.setenv("LB_SANDBOX_ROOT", str(root))
    python = _install_venv(tmp_path)
    _use_bootstrap(lb, tmp_path)
    assert lb.serve_exec_args(root, ["--port", "2482"])[0] == str(python)  # control
    primary, _aux = lb.write_plists(root, "r1", SERVE_PORTS, "http")
    assert plistlib.loads(primary.read_bytes())["ProgramArguments"][0] == str(python)
    shim_file = root / "shim"
    shim_file.write_text("#!/bin/sh\nexit 0\n")
    shim_file.chmod(0o755)
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
        private.write_text("#!/bin/sh\nexit 0\n")
        private.chmod(0o755)
        python.unlink()
        python.symlink_to(private)
    else:
        elsewhere = tmp_path / "elsewhere" / "python3"
        elsewhere.parent.mkdir(mode=0o755)
        elsewhere.write_bytes(Path(os.path.realpath(python)).read_bytes())
        elsewhere.chmod(0o755)
        python.unlink()
        python.symlink_to(elsewhere)
    with pytest.raises(lb.Refused):
        lb.serve_exec_args(root, ["--port", "2482"])


# ------------------------------------------------ S1-3: review of b72e4560b (P1 x3) and cf915e10 (M2, M3)


def test_scan_never_reads_a_hard_link_to_a_live_file_in_an_output_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding (P1, b72e4560b:1128): outside the sandbox root a second hard link was read, so a link in a
    test-owned output dir to live state/front.json read the live inode and the scan said complete and clean.
    A file that is an inode alias of a live agent-lb file is never read: the scan is incomplete, a gate failure.
    A multi-link file that is no live file (a local git clone's objects in TMPDIR) is still read."""
    lb = _load()
    _, lb_home = _home_layout(tmp_path, monkeypatch, lb)
    out = tmp_path / "out"
    out.mkdir()
    (out / "result.json").write_text("{}")
    os.link(lb_home / "state" / "front.json", out / "front.json")
    result = lb.scan_paths([out], [FAKE_TOKEN])
    assert (result["complete"], result["files_scanned"]) == (False, 1)
    assert result["unreadable_paths"] == [str((out / "front.json").resolve())]
    live_itself = lb.scan_paths([lb_home / "encryption.key"], [FAKE_TOKEN])  # control: a live file at its own
    assert (live_itself["complete"], live_itself["files_scanned"]) == (True, 1)  # path is read (step 7 scans it)
    other = tmp_path / "clone-object"  # control: a second name for a file that is not live is read
    (tmp_path / "origin-object").write_text("blob")
    os.link(tmp_path / "origin-object", other)
    assert lb.scan_paths([other], [FAKE_TOKEN])["complete"] is True


@pytest.mark.parametrize("target", ["encryption.key", "state"])
def test_scan_never_follows_a_scan_top_that_is_a_link_to_live_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """Finding (P1, b72e4560b:1093): an explicit scan top was resolved before it was opened, so a top that is a
    link to the live key (or live state/) was read and could report found false, complete true. The top is
    opened with no link followed: refused, nothing read, the scan incomplete."""
    lb = _load()
    _, lb_home = _home_layout(tmp_path, monkeypatch, lb)
    top = tmp_path / "out-link"
    top.symlink_to(lb_home / target)
    result = lb.scan_paths([top], [FAKE_TOKEN])
    assert (result["complete"], result["files_scanned"], result["found"]) == (False, 0, False)


@pytest.mark.parametrize("run_id", ["state", "data", "bin", "runtime", "logs", "custom-live-dir"])
def test_a_run_id_naming_a_live_agent_lb_directory_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_id: str
) -> None:
    """Finding (P1, b72e4560b:178): run `state` under a sandboxes dir linked to ~/.agent-lb names live state.
    A run id that is a live agent-lb directory's name, fixed or present in the live home, is refused."""
    lb = _load()
    _, lb_home = _home_layout(tmp_path, monkeypatch, lb)
    (lb_home / "custom-live-dir").mkdir()
    with pytest.raises(lb.Refused):
        lb.check_run_id(run_id)
    assert lb.check_run_id("lbsbx-20261007t170000z-42") == "lbsbx-20261007t170000z-42"  # control


@pytest.mark.skipif(sys.platform != "darwin", reason="teardown asks launchctl, which is macOS")
@pytest.mark.parametrize("when", ["after-detach", "before-stop"])
def test_teardown_never_deletes_through_a_sandboxes_dir_swapped_for_a_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mounted_root: Path, when: str
) -> None:
    """Findings (P1, b72e4560b:178 and :2763; M3, cf915e10:2253). after-detach: once the volume detaches, the
    sandboxes dir is renamed aside and replaced by a link to a live home holding a directory of the run's name;
    check_root resolved both sides, accepted it, and rmtree deleted the live directory. before-stop: the
    sandboxes dir is a link to a directory holding a stamped root of that name; stop deleted it. The root is
    removed only by name in the sandboxes dir pinned at the start of stop, and a linked sandboxes dir is refused.

    detach_volume is replaced to run the attacker's swap at the exact point of the race; nothing else is faked.
    """
    lb = _load()
    root = _teardown_fixture(tmp_path, monkeypatch, lb, mounted_root)
    sandboxes = root.parent
    live_home = tmp_path / "live-home"
    (live_home / root.name / "state").mkdir(parents=True)
    live_file = live_home / root.name / "state" / "front.json"
    live_file.write_text('{"preferred": 2457}')
    aside = tmp_path / "sandboxes-aside"
    if when == "after-detach":
        real_detach = lb.detach_volume

        def detach_and_swap(path: Path) -> dict:
            done = real_detach(path)
            sandboxes.rename(aside)
            sandboxes.symlink_to(live_home, target_is_directory=True)
            return done

        monkeypatch.setattr(lb, "detach_volume", detach_and_swap)
    else:
        (live_home / root.name / "sandbox.json").write_text(json.dumps({"run_id": root.name, "ports": {}}))
        sandboxes.rename(aside)
        sandboxes.symlink_to(live_home, target_is_directory=True)
    result = lb.teardown(root.name, None)
    assert live_file.read_text() == '{"preferred": 2457}', "teardown deleted a live directory through the link"
    if when == "after-detach":
        assert not (aside / root.name).exists(), "the run's own root, in the pinned sandboxes dir, is removed"
        assert result["root_exists"] is False
    else:
        assert "link" in result.get("refused", ""), "a linked sandboxes dir is refused, nothing deleted"
        assert (aside / root.name / "data" / "encryption.key").exists()


def _published_root(lb: ModuleType, run_id: str):
    """The run's private dir and the mountpoint start made in it and published at <sandboxes>/run_id."""
    lb.sandboxes_fd(create=True)
    priv = lb.make_private(run_id)
    fd, ident = lb.make_root(lb.SANDBOXES / run_id, priv)
    os.close(fd)
    return priv, lb.SANDBOXES / run_id, ident


def test_image_swapped_for_a_link_is_never_chmoded_or_attached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Finding (P1, b72e4560b:563): after hdiutil create, the image entry swapped for a link to the installed
    lb-restart was chmod'ed by name (the live helper lost its execute bits) and handed to attach. The image is
    opened with no link followed and never chmod'ed; a swapped entry is refused before attach.

    hdiutil is the external edge, so it is faked: create makes the image (in the run's private dir, the directory
    lb-sandbox runs it in) and then the attacker swaps it.
    """
    import stat as st

    lb = _load()
    _, lb_home = _home_layout(tmp_path, monkeypatch, lb)
    helper = lb_home / "bin" / "lb-restart"
    helper.parent.mkdir()
    helper.write_text("#!/bin/sh\nexit 0\n")
    helper.chmod(0o755)
    calls: list[list[str]] = []

    def fake_hdiutil(priv, swap: bool):
        def run(argv, **kwargs):
            calls.append([str(a) for a in argv])
            if argv[1] == "create":  # as hdiutil does under the umask 077 lb-sandbox sets: private, magic first
                image = Path(lb.fd_path(priv.fd)) / Path(argv[-1]).name
                fd = os.open(image, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.write(fd, lb.IMAGE_MAGIC + b" sparse image")
                os.close(fd)
                if swap:
                    image.unlink()
                    image.symlink_to(helper)
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        return run

    priv, root, ident = _published_root(lb, "lbsbx-unit-swap")
    monkeypatch.setattr(lb.subprocess, "run", fake_hdiutil(priv, swap=True))
    with pytest.raises((lb.Refused, lb.Unhealthy)):
        lb.attach_volume(root, root.name, priv, ident)
    assert st.S_IMODE(helper.stat().st_mode) == 0o755, "chmod followed the swapped image to the live helper"
    assert [c[1] for c in calls] == ["create"], "a swapped image must never reach attach"
    assert "image" not in priv.ids, "a swapped image was recorded as the run's"
    calls.clear()
    priv2, root2, ident2 = _published_root(lb, "lbsbx-unit-ctl")
    monkeypatch.setattr(lb.subprocess, "run", fake_hdiutil(priv2, swap=False))  # control: the image is attached
    with pytest.raises(lb.Unhealthy):  # the fake attach mounts nothing at the root
        lb.attach_volume(root2, root2.name, priv2, ident2)
    image = Path(lb.fd_path(priv2.fd)) / lb.IMAGE_NAME
    assert [c[1] for c in calls] == ["create", "attach"] and calls[1][-1] == lb.IMAGE_NAME
    assert st.S_IMODE(image.stat().st_mode) == 0o600 and priv2.ids["image"] == lb.identity(image.stat())


@pytest.mark.skipif(sys.platform != "darwin", reason="hdiutil and Seatbelt are macOS")
@pytest.mark.parametrize("when", ["before-boot", "after-boot"])
def test_app_store_opens_never_follow_a_swapped_data_dir(tmp_path: Path, mounted_root: Path, when: str) -> None:
    """Finding (M2, cf915e10:1457): after _serve's custody checks, data/ swapped for a link to live data led the
    app's store and key opens (by name) into live custody. _boot opens data/ by descriptor from the pinned root,
    refuses any directory but the one _serve checked (before-boot), and runs the app inside it with relative
    data paths, so a swap once the app is up (after-boot) changes nothing it opens.

    Integration: the real _serve path, the real _boot exec and Seatbelt, a stand-in app. The fake live data dir
    sits outside the home, where the root's profile does not deny writes, so only the fix keeps it clean.
    """
    import time

    sandboxes, root, live_plist = _fake_sandbox(tmp_path)
    live_data = tmp_path / "live-data"
    live_data.mkdir()
    swap = ""
    if when == "before-boot":
        swap = f"os.rename(root / 'data', root / 'data-aside')\nos.symlink({str(live_data)!r}, root / 'data')\n"
    else:
        (root / "swap-data-to").write_text(str(live_data))
    driver = (
        "import importlib.machinery, importlib.util, os, sys\n"
        "from pathlib import Path\n"
        f"loader = importlib.machinery.SourceFileLoader('lbs', {str(tmp_path / 'installed-bin' / 'lb-sandbox')!r})\n"
        "spec = importlib.util.spec_from_loader('lbs', loader)\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "loader.exec_module(m)\n"
        f"m.SANDBOXES = Path({str(sandboxes)!r})\n"
        f"m.LIVE_PLIST = Path({str(live_plist)!r})\n"
        f"root = Path({str(root)!r})\n"
        "python, args, env, token = m.serve_exec_args(root, ['--host', '127.0.0.1', '--port', '2481'])\n"
        f"{swap}"
        "fd = m.token_pipe(token)\n"
        "os.chdir(root / 'runtime')\n"
        "os.execve(python, [*args[:5], '--token-fd', str(fd), *args[5:]], env)\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "AGENT_LB_FEDERATION_TOKEN"}
    env["LB_SANDBOX_ROOT"] = str(root)
    proc = subprocess.Popen([sys.executable, "-c", driver], env=env, stderr=subprocess.PIPE, start_new_session=True)
    report = root / "boot-report.json"
    try:
        deadline = time.monotonic() + 30
        while not report.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        started = report.exists()
    finally:
        (root / "stop").touch()
        try:
            proc.wait(30)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, 9)
            proc.wait()
    assert sorted(p.name for p in live_data.iterdir()) == [], "the app opened its data through the swapped link"
    if when == "before-boot":
        assert (started, proc.returncode) == (False, 2), "_boot must refuse a data dir other than the one checked"
    else:
        assert started, f"control: the app must start (exit {proc.returncode})"
        assert (root / "data-aside" / "app-touched").exists(), "the app's data opens stay in the checked dir"


# ---------------------------------------------------------------- S2-3 fix round (live-safety review, 84a63c41)


def test_both_jobs_run_the_installed_lb_sandbox_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """M1 and M2 (lb-restart:1013,1007; lb-sandbox:1555): the plists ran <root>/bin/lb-sandbox without -I, so a
    regular file replacing it, or a sitecustomize.py on the plist's PYTHONPATH (<root>/runtime), ran before any
    guard. Both jobs now run `<venv python> -I <installed lb-sandbox>`: no program or script path under the root."""
    lb = _load()
    root = tmp_path / "sandboxes" / "r1"
    root.mkdir(parents=True, mode=0o700)
    root.parent.chmod(0o700)  # lb-sandbox start makes the sandboxes dir 0700, whatever the umask
    python = _install_venv(tmp_path)
    installed = _use_bootstrap(lb, tmp_path)
    paths = lb.write_plists(root, "r1", SERVE_PORTS, "http")
    heads = [plistlib.loads(path.read_bytes())["ProgramArguments"][:5] for path in paths]
    assert heads == [
        [str(python), "-I", str(installed), "_serve", str(root)],
        [str(python), "-I", str(installed), "_aux", str(root)],
    ]
    with pytest.raises(lb.Refused, match="inside the sandboxes dir"):
        lb.check_bootstrap(root / "bin" / "lb-sandbox", root)


def test_boot_never_runs_from_a_copy_inside_the_sandboxes_dir(tmp_path: Path) -> None:
    """M2 on lb-sandbox's side: _boot used to require the copy inside its root, which the confined app can
    rewrite. Run from such a copy it now refuses before it touches the root."""
    sandboxes = tmp_path / "sandboxes"
    root = sandboxes / "r1"
    (root / "bin").mkdir(parents=True)
    copy = root / "bin" / "lb-sandbox"
    copy.write_bytes(SCRIPT.read_bytes())
    copy.chmod(0o755)
    loader = importlib.machinery.SourceFileLoader("lb_sandbox_root_copy", str(copy))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    in_root = importlib.util.module_from_spec(spec)
    loader.exec_module(in_root)
    with pytest.raises(in_root.Refused, match="inside the sandboxes dir"):
        in_root.boot_app(root, ["--token-fd", "0", "--port", "2481"])


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt is macOS")
def test_a_confined_process_reads_its_own_root_under_the_agent_lb_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1 (84a63c41, lb-sandbox:240,318): the no-follow walk opened every ancestor for reading, and the root's
    profile denies reads under ~/.agent-lb, so a confined _boot or _aux read its own sandbox.json as {} and
    refused a valid sandbox. Ancestors are now opened search-only. The layout is the real one, <home>/.agent-lb/
    sandboxes/<run>, under a test home; live custody stays unreadable and a planted link is still refused."""
    lb = _load()
    root, lb_home = _home_layout(tmp_path, monkeypatch, lb)
    (root / "sandbox.json").write_text(json.dumps({"run_id": "r1", "ports": SERVE_PORTS}))
    (root / "linked.json").symlink_to(root / "sandbox.json")
    got = lb.run_confined(
        root,
        lambda: [lb.read_root_json(root, "sandbox.json"), lb.read_root_json(root, "linked.json")],
        reads=True,
    )
    assert got == [{"run_id": "r1", "ports": SERVE_PORTS}, {}]
    with pytest.raises(lb.Unhealthy):  # control: live state under the same home stays unreadable
        lb.run_confined(root, lambda: (lb_home / "state" / "front.json").read_text(), reads=True)


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt is macOS")
def test_a_confined_process_reads_its_own_root_in_the_real_sandboxes_dir() -> None:
    """P1 against the real layout and profile: a test-owned root directly under the real ~/.agent-lb/sandboxes,
    confined with the real constants. Skipped where that dir is not this user's real directory. The root is
    removed by its asserted path; nothing else under ~/.agent-lb is opened for writing."""
    import shutil
    import uuid

    lb = _load()
    sandboxes = lb.SANDBOXES
    try:
        lb.sandboxes_fd(sandboxes)
    except OSError:
        pytest.skip("no private real ~/.agent-lb/sandboxes on this host")
    root = sandboxes / f"lbsbx-unit-{uuid.uuid4().hex[:12]}"
    os.mkdir(root, 0o700)
    try:
        (root / "sandbox.json").write_text(json.dumps({"run_id": root.name, "unit_test": True}))
        got = lb.run_confined(root, lambda: lb.read_root_json(root, "sandbox.json"), reads=True)
    finally:
        assert root.parent == sandboxes and root.name.startswith("lbsbx-unit-")
        shutil.rmtree(root)
    assert got == {"run_id": root.name, "unit_test": True}


# ------------------------------------------------ S1-3 fix round 2: review of 479849b4 and 435cbabd


def test_scan_never_reads_a_live_file_through_a_linked_parent_of_a_scan_top(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding (lb-sandbox:1480-1497 at 435cbabd): a scan top out/linked-parent/front.json with linked-parent ->
    ~/.agent-lb/state had its parent resolved before the no-follow open, so the live file was read and the scan
    said complete and clean. Only root-owned links (/var -> private/var) are resolved now; a link this user made
    in a top's path is refused and the scan is incomplete."""
    lb = _load()
    _, lb_home = _home_layout(tmp_path, monkeypatch, lb)
    out = tmp_path / "out"
    out.mkdir()
    (out / "linked-parent").symlink_to(lb_home / "state", target_is_directory=True)
    result = lb.scan_paths([out / "linked-parent" / "front.json"], [FAKE_TOKEN])
    assert (result["complete"], result["files_scanned"]) == (False, 0), "a live file was read through the link"
    real = os.path.realpath(tmp_path)
    if not real.startswith("/private/var/"):
        pytest.skip("control needs TMPDIR behind the system link /var -> private/var")
    (out / "plain.json").write_text(f"x {FAKE_TOKEN} y")
    spelled = Path(real[len("/private") :]) / "out" / "plain.json"  # through the root-owned /var link
    control = lb.scan_paths([spelled], [FAKE_TOKEN])
    assert (control["complete"], control["found"], control["files_scanned"]) == (True, True, 1)


@pytest.mark.parametrize("live_file", ["there-before-the-scan", "made-during-the-scan"])
def test_scan_never_reads_a_live_file_hard_linked_after_the_live_inodes_were_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live_file: str
) -> None:
    """Finding (lb-sandbox:1542-1545 at 435cbabd): the live inodes were listed once, on the first multi-link
    file, and only those with a second name then. An unrelated multi-link file early in the walk filled that list;
    a later output file then swapped for a hard link to single-link live state/front.json was not in it and was
    read, and the scan said clean. The list holds every live file, and a multi-link file whose inode changed
    since the list was made refreshes it, so a live file written during the scan (made-during-the-scan) is
    caught too.

    The attacker runs at the race point: after the first file is read, before the walk reaches the second."""
    lb = _load()
    _, lb_home = _home_layout(tmp_path, monkeypatch, lb)
    front = lb_home / "state" / "front.json"
    out = tmp_path / "out"
    out.mkdir()
    (tmp_path / "origin-object").write_text("blob")
    os.link(tmp_path / "origin-object", out / "a-clone-object")  # read first, in name order
    target = out / "z-result.json"
    target.write_text("{}")
    real_scan_fd = lb.scan_fd
    swapped: list[bool] = []

    def scan_then_swap(fd, detector, chunk=4 << 20):
        if not swapped:
            swapped.append(True)
            if live_file == "made-during-the-scan":  # the front rewrites its state file: a new inode, one name
                tmp = front.with_name("front.json.tmp")
                tmp.write_text('{"preferred": 2457}')
                os.rename(tmp, front)
            target.unlink()
            os.link(front, target)
        return real_scan_fd(fd, detector, chunk)

    monkeypatch.setattr(lb, "scan_fd", scan_then_swap)
    result = lb.scan_paths([out], [FAKE_TOKEN])
    assert swapped, "the race point was never reached"
    assert result["unreadable_paths"] == [str(target.resolve())], "the hard link to live state was read"
    assert (result["complete"], result["files_scanned"]) == (False, 1)


@pytest.mark.skipif(sys.platform != "darwin", reason="hdiutil is macOS")
def test_a_live_file_renamed_over_the_new_image_is_never_chmoded_attached_or_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding (lb-sandbox:840, 780-785 at 435cbabd): after hdiutil create, single-link live bin/lb-restart
    renamed over the image entry passed the identity check (a regular file with one link), was chmod'ed 0600
    and lost its execute bit. Nothing is chmod'ed now (hdiutil creates under umask 077), the entry must be the
    private encrypted image this create made before it is recorded, and teardown deletes only the recorded image.

    Integration with the real hdiutil; the attacker's rename runs right after the real create returns."""
    import stat as st

    lb = _load()
    _, lb_home = _home_layout(tmp_path, monkeypatch, lb)
    helper = lb_home / "bin" / "lb-restart"
    helper.parent.mkdir()
    helper.write_text("#!/bin/sh\nexit 0\n")
    helper.chmod(0o755)
    priv, root, ident = _published_root(lb, "lbsbx-unit-image")
    entry = Path(lb.fd_path(priv.fd)) / lb.IMAGE_NAME
    real_run = subprocess.run
    calls: list[str] = []

    def create_then_rename(argv, **kwargs):
        calls.append(str(argv[1]))
        done = real_run(argv, **kwargs)
        if argv[1] == "create":
            os.rename(helper, entry)
        return done

    monkeypatch.setattr(lb.subprocess, "run", create_then_rename)
    with pytest.raises(lb.Refused):
        lb.attach_volume(root, root.name, priv, ident)
    monkeypatch.setattr(lb.subprocess, "run", real_run)
    assert calls == ["create"], "the renamed live file reached attach"
    assert st.S_IMODE(entry.stat().st_mode) == 0o755, "the live helper was chmod'ed"
    assert lb.remove_recorded(priv.fd, lb.IMAGE_NAME, priv.ids.get("image"), "file") is False
    assert entry.read_text() == "#!/bin/sh\nexit 0\n", "the image delete unlinked a file that is no image"
    entry.unlink()
    priv2, root2, ident2 = _published_root(lb, "lbsbx-unit-image2")
    try:  # control: the image the real create makes is attached at the root, then removed by its record
        lb.attach_volume(root2, root2.name, priv2, ident2)
        assert lb.on_own_volume(root2) and lb.on_run_volume(root2, priv2.ids)
    finally:
        detached = lb.detach_volume(root2)
    assert detached == {"volume": "detached"}
    assert lb.remove_recorded(priv2.fd, lb.IMAGE_NAME, priv2.ids["image"], "file") is True
    assert not (Path(lb.fd_path(priv2.fd)) / lb.IMAGE_NAME).exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="teardown asks launchctl; hdiutil is macOS")
@pytest.mark.parametrize("root_kind", ["plain-dir", "volume-with-forged-record"])
def test_teardown_never_removes_live_state_moved_into_the_roots_name_after_the_detach(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest, root_kind: str
) -> None:
    """Finding (lb-sandbox:3290-3292, 3164-3171 at 435cbabd): after the detach, the empty mountpoint renamed
    aside and live ~/.agent-lb/state moved into its name inside the pinned sandboxes dir passed remove_root's
    checks (a directory on the sandboxes dir's device) and was rmtree'd. Only the directory start made (its
    recorded device and inode) is removed, and a root that was its own volume is only rmdir'ed: its mountpoint
    is empty, so even a sandbox.json forged with the live dir's identity deletes nothing (volume-with-forged-record).

    detach_volume runs for real on the volume, then the attacker's moves run at that exact point."""
    lb = _load()
    live_state = tmp_path / "live-home" / "state"
    live_state.mkdir(parents=True)
    (live_state / "front.json").write_text('{"preferred": 2457}')
    mounted_root = request.getfixturevalue("mounted_root")
    if root_kind == "plain-dir":
        root = _teardown_fixture(tmp_path, monkeypatch, lb, mounted_root)
    else:
        root = mounted_root
        # A sandbox.json forged by the confined app to name the live dir: deletes follow only the private record.
        (root / "sandbox.json").write_text(
            json.dumps({"run_id": root.name, "ports": {}, "root_id": lb.identity(live_state.stat())})
        )
        monkeypatch.setattr(lb, "SANDBOXES", root.parent)
        monkeypatch.setattr(lb, "LIVE_PLIST", tmp_path / "no-live.plist")
        monkeypatch.setattr(lb, "load_secrets", lambda root: [FAKE_TOKEN])
        monkeypatch.setattr(lb, "load_key", lambda root: None)
        _record_start(lb, root, key=False)
    real_detach = lb.detach_volume

    def detach_then_move(path: Path) -> dict:
        done = real_detach(path)
        root.rename(root.parent / "aside")
        live_state.rename(root)
        return done

    monkeypatch.setattr(lb, "detach_volume", detach_then_move)
    result = lb.teardown(root.name, None)
    assert (root / "front.json").read_text() == '{"preferred": 2457}', "teardown deleted live state"
    assert (result["root_exists"], result["clean"], result["volume"].get("root_removed")) == (True, False, False)


# ------------------------------------------------ lbsb-4 fix round (S2-3 review, parked findings)


def test_a_startup_hook_on_pythonpath_never_runs_in_the_re_execd_command(tmp_path: Path) -> None:
    """S2-3 review (lb-sandbox:3570): main's re-exec into the agent-lb venv dropped -I and passed PYTHONPATH on, so
    `python -I lb-sandbox stop --run-id R` with PYTHONPATH=<root>/runtime holding a sitecustomize.py ran the hook
    in the re-exec'd process, before any guard. Integration with the real script and interpreters: the command
    re-execs into the installed agent-lb venv's python (read only, never the service), and a run id the command
    refuses keeps it from reading or writing anything. The control runs the same plant without -I and without the
    re-exec, which fires it."""
    lb = _load()
    live_python = lb.LIVE_VENV / "bin" / "python"
    if not live_python.exists() or lb.in_live_venv():
        pytest.skip("needs the installed agent-lb venv to re-exec into, and a test interpreter outside it")
    hook_dir, ran = tmp_path / "runtime", tmp_path / "hook-ran"
    hook_dir.mkdir()
    (hook_dir / "sitecustomize.py").write_text(f"open({str(ran)!r}, 'a').write('ran\\n')\n")
    env = {k: v for k, v in os.environ.items() if k != "LB_SANDBOX_NO_REEXEC"}
    env["PYTHONPATH"] = str(hook_dir)
    argv = ["stop", "--run-id", "Not-A-Run-Id"]
    done = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), *argv], env=env, capture_output=True, timeout=120, start_new_session=True
    )
    assert done.returncode == 2 and b"refused" in done.stdout
    assert not ran.exists(), "a planted sitecustomize.py ran in the re-exec'd lb-sandbox"
    control = subprocess.run(
        [sys.executable, str(SCRIPT), *argv],
        env={**env, "LB_SANDBOX_NO_REEXEC": "1"},
        capture_output=True,
        timeout=120,
        start_new_session=True,
    )
    assert control.returncode == 2 and ran.exists(), "the plant never fires: the assertion above proves nothing"


class _StartStopsHere(Exception):
    """Raised by the faked launchd at the primary's bootstrap: start has handed launchd its job."""


def _start_rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lb: ModuleType) -> tuple[Path, Path]:
    """A home with a live agent-lb (runtime, venv, plist with a fake token, state) for a real `start`; returns
    (agent-lb home, the installed lb-sandbox the jobs run). The live labels are drill labels nothing loads."""
    _, lb_home = _home_layout(tmp_path, monkeypatch, lb)
    home = lb_home.parent
    _install_venv(lb_home)
    (lb.LIVE_RUNTIME / "app").mkdir(parents=True)
    (lb.LIVE_RUNTIME / "app" / "__init__.py").write_text("")
    lb.LIVE_PLIST.parent.mkdir(parents=True)
    lb.LIVE_PLIST.write_bytes(plistlib.dumps({"EnvironmentVariables": {lb.TOKEN_ENV: FAKE_TOKEN}}))
    for name, value in {
        "LIVE_VENV": lb.LIVE_RUNTIME / ".venv",
        "LIVE_FRONT_STATE": lb_home / "state" / "front.json",
        "MANAGED_ROUTING": lb_home / "managed" / "coding-agents",
        "LIVE_LABEL": "com.agent-lb.drill.unit-no-live",
        "LIVE_FRONT_LABEL": "com.agent-lb.drill.unit-no-live-front",
    }.items():
        monkeypatch.setattr(lb, name, value)
    return lb_home, _use_bootstrap(lb, home)


def _fake_launchd(real_run, on_bootstrap, seen: list[list[str]] | None = None):
    """launchctl as the OS edge: `list` shows no job, print and bootout find nothing, bootstrap calls
    on_bootstrap(argv, plist path). Every other command runs for real."""

    def launchd(argv, *args, **kwargs):
        if not argv or os.path.basename(str(argv[0])) != "launchctl":
            return real_run(argv, *args, **kwargs)
        if seen is not None:
            seen.append([str(a) for a in argv])
        if argv[1] == "list":
            return subprocess.CompletedProcess(argv, 0, "PID\tStatus\tLabel\n", "")
        if argv[1] != "bootstrap":
            return subprocess.CompletedProcess(argv, 113, "", "")
        return on_bootstrap(argv, Path(argv[3]))

    return launchd


def _run_start(lb: ModuleType, argv: list[str], root: Path) -> int | str:
    """lb-sandbox start in this process ("refused" when it raises Refused); the volume is detached after."""
    import signal as signals

    handlers = {sig: signals.getsignal(sig) for sig in (signals.SIGTERM, signals.SIGINT, signals.SIGHUP)}
    try:
        return lb.cmd_start(lb.build_parser().parse_args(argv))
    except lb.Refused:
        return "refused"
    finally:
        for sig, handler in handlers.items():
            signals.signal(sig, handler)
        if lb.on_own_volume(root):
            lb.detach_volume(root)


def _git_repo(path: Path) -> str:
    """A tiny agent-lb repo: one commit with app/, config/ and scripts/; returns its sha."""
    for rel, text in {
        "app/__init__.py": "",
        "app/marker.py": "FROM_REF = True\n",
        "config/unit.json": "{}\n",
        "scripts/agent-lb-front.mjs": "// front\n",
        "README.md": "not runtime code\n",
    }.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text)
    git = ["git", "-C", str(path), "-c", "user.name=unit", "-c", "user.email=unit@example.invalid"]
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "unit"]):
        subprocess.run([*git, *args], check=True, capture_output=True, timeout=60)
    return subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


@pytest.mark.skipif(sys.platform != "darwin", reason="hdiutil, Seatbelt and launchd are macOS")
@pytest.mark.parametrize("source", ["live", "ref"])
def test_start_hands_launchd_only_the_jobs_it_made_never_a_plist_the_aux_rewrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, source: str
) -> None:
    """S2-3 review (lb-sandbox:2234): start bootstrapped the aux before the primary, both from plists under the
    root. The aux (and the front it starts) can write anywhere in the root, so a compromised one could rewrite the
    primary's plist to an unconfined payload that start then loaded. launchd now gets each job from bytes start
    made, through a private file outside every root (the run's private dir), removed once loaded.

    source "ref" (agent-lb-5): `start --from-ref REF` runs the sandbox on that commit's code, exported from git
    into the root, while accounts still mirror live: the runtime the aux finds is the ref's files, not live's.

    Integration through the real `start`: a real volume (hdiutil), the real confined populate, real ports, a real
    git repo. Only launchd is faked (the OS edge): its bootstrap records the plist it is handed, the aux's runs by
    writing its ready state and rewriting the primary's plist in the root, and the primary's ends the start (its
    teardown runs for real and removes the private dir)."""
    lb = _load()
    lb_home, script = _start_rig(tmp_path, monkeypatch, lb)
    run_id = "lbsbx-unit-boot"
    root = lb.SANDBOXES / run_id
    label = f"com.agent-lb.drill.sbx-{run_id}"
    hostile = {"Label": label, "ProgramArguments": ["/bin/sh", "-c", "echo unconfined"], "RunAtLoad": True}
    handed: list[tuple[str, dict]] = []
    runtime_seen: dict[str, bool] = {}

    def bootstrap(argv, path: Path):
        body = plistlib.loads(path.read_bytes())
        handed.append((str(path), body))
        if body["Label"] == label:
            raise _StartStopsHere()
        runtime_seen["marker"] = (root / "runtime" / "app" / "marker.py").is_file()
        runtime_seen["readme"] = (root / "runtime" / "README.md").exists()
        # The aux job runs: confined to the root, it may write anything in it.
        (root / "state" / "aux.json").write_text(json.dumps({"ready": True}))
        (root / "launchd" / f"{label}.plist").write_bytes(plistlib.dumps(hostile))
        return subprocess.CompletedProcess(argv, 0, "", "")

    argv = ["start", "--run-id", run_id, "--from-live"]
    sha = None
    if source == "ref":
        repo = tmp_path / "agent-lb-repo"
        sha = _git_repo(repo)
        argv = ["start", "--run-id", run_id, "--from-ref", "HEAD", "--repo", str(repo)]
    monkeypatch.setattr(lb.subprocess, "run", _fake_launchd(subprocess.run, bootstrap))
    code = _run_start(lb, argv, root)
    out = json.loads(capsys.readouterr().out)
    assert code == lb.EXIT_UNHEALTHY and "_StartStopsHere" in out["error"]
    assert [body["Label"] for _, body in handed] == [f"{label}-aux", label]
    primary_path, primary = handed[1]
    assert primary["ProgramArguments"][1:4] == ["-I", str(script), "_serve"], "launchd got the rewritten plist"
    assert primary["ProgramArguments"][4] == str(root) and primary != hostile
    for path, _ in handed:
        assert not Path(path).is_relative_to(root), "launchd was handed a plist from inside the root"
        assert not os.path.lexists(path), "the private copy outlived its bootstrap"
    assert out["teardown"]["root_exists"] is False and out["teardown"]["private_exists"] is False
    assert out["teardown"]["clean"] is True
    if source == "ref":
        assert runtime_seen == {"marker": True, "readme": False}, "the root's runtime is not the ref's files"
        assert out["runtime"] == {"kind": "ref", "ref": "HEAD", "sha": sha, "repo": str(repo)}
    else:
        assert runtime_seen == {"marker": False, "readme": False}
        assert out["runtime"]["kind"] == "live"


@pytest.mark.parametrize("ref", ["-c", "HEAD..x", "a b", "--output=x", ""])
def test_start_refuses_a_ref_that_is_not_a_plain_name_before_making_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ref: str
) -> None:
    """`--from-ref` goes to git as an argument: an option-looking or range ref is refused, nothing is created."""
    lb = _load()
    _start_rig(tmp_path, monkeypatch, lb)
    monkeypatch.setattr(lb.subprocess, "run", _fake_launchd(subprocess.run, lambda argv, path: pytest.fail("ran")))
    before = sorted(os.listdir(lb.SANDBOXES))
    assert _run_start(lb, ["start", "--run-id", "lbsbx-unit-ref", f"--from-ref={ref}"], lb.SANDBOXES / "x") in (
        "refused",
        lb.EXIT_REFUSED,
    )
    assert sorted(os.listdir(lb.SANDBOXES)) == before


@pytest.mark.skipif(sys.platform != "darwin", reason="hdiutil is macOS")
def test_restart_runs_lb_restart_isolated_from_a_planted_pythonpath(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mounted_root: Path, capsys: pytest.CaptureFixture
) -> None:
    """Same class as the re-exec finding (S2-3, lb-sandbox:3570): `lb-sandbox restart` started lb-restart (which
    signals and kickstarts) without -I and with the caller's whole environment, so PYTHONPATH=<root>/runtime ran a
    planted sitecustomize.py inside it. Integration with the real lb-restart beside this script, which then
    refuses this sandbox (it is not under the real home) before touching anything."""
    lb = _load()
    monkeypatch.setattr(lb, "SANDBOXES", mounted_root.parent)
    (mounted_root / "sandbox.json").write_text(json.dumps({"run_id": mounted_root.name}))
    hook_dir, ran = tmp_path / "hook", tmp_path / "hook-ran"
    hook_dir.mkdir()
    (hook_dir / "sitecustomize.py").write_text(f"open({str(ran)!r}, 'a').write('ran\\n')\n")
    monkeypatch.setenv("PYTHONPATH", str(hook_dir))
    args = lb.build_parser().parse_args(["restart", "--run-id", mounted_root.name, "--reason", "unit"])
    code = lb.cmd_restart(args)
    out = json.loads(capsys.readouterr().out)
    assert code == lb.EXIT_UNHEALTHY and out["lb_restart_exit"] == 2, "lb-restart did not run to its refusal"
    assert not ran.exists(), "a planted sitecustomize.py ran inside lb-restart"


# ------------------------------------------------ sys-lb-sandbox-5 fix round (lbsb-5 review: M1-M4, P1, custody)


def _fake_live_file(tmp_path: Path) -> Path:
    """A single-link stand-in for live ~/.agent-lb/state/front.json, on the sandboxes dir's volume."""
    live = tmp_path / "live-home" / "state" / "front.json"
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text('{"preferred": 2457}')
    return live


@pytest.mark.skipif(sys.platform != "darwin", reason="hdiutil, Seatbelt and launchd are macOS")
@pytest.mark.parametrize("attack", ["renamed-over-the-job-file", "live-dir-at-the-staging-name", "none"])
def test_launchd_staging_never_deletes_or_chmods_live_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, attack: str
) -> None:
    """lbsb-5 review M1 (lb-sandbox:2391-2392 at 26d90565): after the job file's identity check, single-link live
    state/front.json renamed over job.plist was unlinked by the cleanup (its directory was writable again). M3
    (2355-2357): with <sandboxes>/.launchd renamed aside and a live directory moved into its name before bootstrap,
    only ownership was checked and the live directory was chmod'ed 0700. The job file now lives in the run's
    private dir, is renamed aside and checked by descriptor before any unlink, and no shared staging dir is used.

    Integration through the real `start` (real volume, confined populate); launchd is the faked OS edge.
    renamed-over-the-job-file: the attacker's rename runs at the cleanup's unlink, aimed at the job file's own name.
    live-dir-at-...: a live directory sits at the old staging name when start runs. none is the control."""
    lb = _load()
    _start_rig(tmp_path, monkeypatch, lb)
    run_id = "lbsbx-unit-stage"
    root = lb.SANDBOXES / run_id
    label = f"com.agent-lb.drill.sbx-{run_id}"
    live = _fake_live_file(tmp_path)
    staging = lb.SANDBOXES / ".launchd"
    if attack == "live-dir-at-the-staging-name":
        staging.mkdir()
        staging.chmod(0o755)
        os.rename(live, staging / "front.json")
        live = staging / "front.json"
    handed: list[Path] = []
    armed: list[bool] = []

    def bootstrap(argv, path: Path):
        handed.append(path)
        if plistlib.loads(path.read_bytes())["Label"] == label:
            raise _StartStopsHere()
        (root / "state" / "aux.json").write_text(json.dumps({"ready": True}))
        armed.append(attack == "renamed-over-the-job-file")
        return subprocess.CompletedProcess(argv, 0, "", "")

    real_unlink = os.unlink
    moved: list[Path] = []

    def unlink_after_rename(path, *, dir_fd=None):
        if armed and armed[0] and dir_fd is not None and lb.fd_path(dir_fd) == str(handed[0].parent):
            armed[0] = False
            os.rename(live, handed[0])  # the attacker aims at the job file's own name, right before the unlink
            moved.append(handed[0])
        real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(lb.subprocess, "run", _fake_launchd(subprocess.run, bootstrap))
    monkeypatch.setattr(lb.os, "unlink", unlink_after_rename)
    code = _run_start(lb, ["start", "--run-id", run_id, "--from-live"], root)
    monkeypatch.setattr(lb.os, "unlink", real_unlink)
    out = json.loads(capsys.readouterr().out)
    assert code == lb.EXIT_UNHEALTHY and "_StartStopsHere" in out["error"]
    if attack == "renamed-over-the-job-file":
        assert moved, "the race point was never reached"
        live = moved[0]
    if attack != "none":
        assert live.is_file() and live.read_text() == '{"preferred": 2457}', "start deleted a live file"
    if attack == "live-dir-at-the-staging-name":
        assert oct(staging.stat().st_mode & 0o777) == "0o755", "start chmod'ed a live directory"
        assert sorted(os.listdir(staging)) == ["front.json"], "start wrote into a live directory"
    if attack == "none":
        assert out["teardown"]["clean"] is True and not any(os.path.lexists(p) for p in handed)


@pytest.mark.skipif(sys.platform != "darwin", reason="st_birthtime, Seatbelt and launchd are macOS")
@pytest.mark.parametrize(
    "swap", ["live-dir-for-new-root", "live-key-into-root", "live-dir-after-publish", "live-file-into-root"]
)
def test_a_failed_start_never_adopts_or_empties_live_state_moved_into_its_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, swap: str
) -> None:
    """lbsb-5 review M4 (lb-sandbox:2501-2511 at 26d90565): acquire_new_root accepted an empty live directory of
    this user on the same volume, made up to 2 s before the mkdir and moved into the root's name, and chmod'ed and
    recorded it. M2 (3641): with attach failed, the failed start's cleanup unlinked a live encryption.key moved
    into data/ of the recorded unmounted root, a key start never made. The root is now made inside the run's
    private dir and must be born after it; a failed start deletes only what it recorded (no key was made, so
    none is unlinked, and an unmounted root is only rmdir'ed by its recorded identity).

    Integration through the real `start`; hdiutil is the faked OS edge (its create fails after the attacker's
    moves, as an attach failure does) and the attacker's moves run at the root's mkdir or at that create.
    live-dir-after-publish and live-file-into-root are the lbsb-4 regressions, kept."""
    import time as clock

    lb = _load()
    _start_rig(tmp_path, monkeypatch, lb)
    run_id = "lbsbx-unit-adopt"
    root = lb.SANDBOXES / run_id
    live = tmp_path / "home" / "live-state"
    (live / "data").mkdir(parents=True)
    (live / "front.json").write_text('{"preferred": 2457}')
    (live / "data" / "encryption.key").write_bytes(b"live key")
    empty_live = tmp_path / "home" / "live-empty"
    empty_live.mkdir()
    empty_live.chmod(0o755)
    clock.sleep(0.2)  # the live directory is older than anything start makes, but well within the old 2 s slack
    real_mkdir, real_run = os.mkdir, subprocess.run
    placed: list[Path] = []

    def mkdir_then_swap(path, mode=0o777, *, dir_fd=None):
        real_mkdir(path, mode, dir_fd=dir_fd)
        if swap == "live-dir-for-new-root" and dir_fd is not None and path in (run_id, "root") and not placed:
            parent = Path(lb.fd_path(dir_fd))
            os.rename(parent / path, parent / "aside")
            os.rename(empty_live, parent / path)
            placed.append(parent / path)

    def hdiutil(argv, *args, **kwargs):
        if not argv or argv[0] != "/usr/bin/hdiutil":
            return real_run(argv, *args, **kwargs)
        if argv[1] == "create":
            if swap == "live-key-into-root":
                os.rename(live / "data", root / "data")
            elif swap == "live-dir-after-publish":
                os.rename(root, root.parent / "aside")
                os.rename(live, root)
            elif swap == "live-file-into-root":
                os.rename(live / "front.json", root / "front.json")
        return subprocess.CompletedProcess(argv, 1, b"", b"")  # the image is never made: attach fails

    monkeypatch.setattr(lb.os, "mkdir", mkdir_then_swap)
    monkeypatch.setattr(lb.subprocess, "run", _fake_launchd(hdiutil, lambda argv, path: pytest.fail("bootstrap")))
    code = _run_start(lb, ["start", "--run-id", run_id, "--from-live"], root)
    monkeypatch.setattr(lb.os, "mkdir", real_mkdir)
    out = capsys.readouterr().out
    if swap == "live-dir-for-new-root":
        assert placed, "the race point was never reached"
        assert placed[0].is_dir(), "the failed start deleted a live directory"
        assert oct(placed[0].stat().st_mode & 0o777) == "0o755", "start chmod'ed live state"
        assert code in ("refused", lb.EXIT_REFUSED), "start owned a directory it did not make"
        return
    teardown = json.loads(out)["teardown"]
    assert code == lb.EXIT_UNHEALTHY
    if swap == "live-key-into-root":
        key = root / "data" / "encryption.key"
        assert key.is_file() and key.read_bytes() == b"live key", "the failed start unlinked a live key"
        assert teardown["custody"]["key_unlinked"] == "never created"
    elif swap == "live-dir-after-publish":
        assert (root / "front.json").read_text() == '{"preferred": 2457}', "the failed start deleted live state"
        assert (root / "data" / "encryption.key").read_bytes() == b"live key"
    else:
        assert (root / "front.json").read_text() == '{"preferred": 2457}', "the failed start deleted live state"
    assert teardown["volume"]["root_removed"] is False and teardown["clean"] is False


@pytest.mark.skipif(sys.platform != "darwin", reason="launchctl is macOS")
def test_no_code_path_prints_a_live_launchd_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mounted_root: Path):
    """Custody floor (lbsb-5 review FIRST, lb-sandbox:1086, 1098, 2408 at 26d90565): `launchctl print` of the live
    agent-lb jobs (start --from-live's live identity, label checks) carried their whole environment, the federation
    token included, into lb-sandbox. Live jobs are read only from the bare `launchctl list` table; print is refused
    for any job but a sandbox's own, at the one place lb-sandbox runs commands.

    launchctl is the faked OS edge: list shows the live jobs and the run's own, print answers with an environment
    holding a token. live_identity, the label checks and a whole teardown run through it."""
    lb = _load()
    label = lb.labels_for(mounted_root.name)[0]
    table = f"PID\tStatus\tLabel\n4242\t0\t{lb.LIVE_LABEL}\n4343\t0\t{lb.LIVE_FRONT_LABEL}\n-\t0\t{label}\n"
    seen: list[list[str]] = []
    real_run = subprocess.run

    def launchctl(argv, *args, **kwargs):
        if not argv or os.path.basename(str(argv[0])) != "launchctl":
            return real_run(argv, *args, **kwargs)
        seen.append([str(a) for a in argv])
        if argv[1] == "list":
            return subprocess.CompletedProcess(argv, 0, table, "")
        if argv[1] == "print":
            env = f"\tpid = 4242\n\tenvironment = {{\n\t\t{lb.TOKEN_ENV} => {FAKE_TOKEN}\n\t}}\n"
            return subprocess.CompletedProcess(argv, 0, env, "")
        return subprocess.CompletedProcess(argv, 113, "", "")

    monkeypatch.setattr(lb.subprocess, "run", launchctl)
    live = lb.live_identity()
    assert (live["primary_pid"], live["front_pid"]) == (4242, 4343)
    assert lb.label_loaded(lb.LIVE_LABEL) and not lb.label_loaded("com.aneyman.agent-lb-nothing")
    assert [argv for argv in seen if argv[1] == "print"] == [], "live identity printed a live job"
    root = _teardown_fixture(tmp_path, monkeypatch, lb, mounted_root)
    result = lb.teardown(root.name, None)
    printed = [argv[2] for argv in seen if argv[1] == "print"]
    assert printed == [f"gui/{os.getuid()}/{label}"], f"launchctl print reached {printed}"
    assert result["bootout"][label].startswith("refused")  # the printed job does not run this root: left alone
    with pytest.raises(lb.Refused):
        lb.run(["/bin/launchctl", "print", f"gui/{os.getuid()}/{lb.LIVE_LABEL}"])
    with pytest.raises(lb.Refused):
        lb.own_job_print(lb.LIVE_LABEL, root.name)
    assert [argv for argv in seen if argv[1] == "print" and lb.LIVE_LABEL in argv[2]] == []


@pytest.mark.parametrize("shift", [0, 1, 2])
@pytest.mark.parametrize("wrap", ["mime", "crlf-indented"])
def test_scan_and_export_find_a_token_in_base64_wrapped_onto_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shift: int, wrap: str
) -> None:
    """lbsb-5 review P1 (lb-sandbox:1546, 2174 at 26d90565): the synthetic 72-byte token, base64-encoded with
    base64.encodebytes (MIME: 76-character lines) into <root>/logs/mime-token.log, was not found: the scan said
    found=false, complete=true, and the export gate copied all 98 encoded bytes. The plaintext patterns needed
    contiguous bytes, and base64 runs were only decoded to look for keyed ciphertext.

    The detector is a pure algorithm with many edge cases (byte alignment, line endings, read boundaries), so it is
    driven directly here; the scan and the export run over real files. Every read size from 1 byte up must find
    it: a wrapped encoding spans more bytes than the encoded pattern."""
    lb = _load()
    root = tmp_path / "sandboxes" / "r1"
    (root / "logs").mkdir(parents=True)
    root.parent.chmod(0o700)
    monkeypatch.setattr(lb, "SANDBOXES", root.parent)
    encoded = base64.encodebytes(b"\0" * shift + FAKE_TOKEN.encode())
    if wrap == "crlf-indented":
        encoded = encoded.replace(b"\n", b"\r\n  ")
    blob = b"INFO request body follows\n" + encoded + b"INFO done\n"
    assert FAKE_TOKEN.encode() not in blob and b"\n" in encoded.strip()
    (root / "logs" / "mime-token.log").write_bytes(blob)
    scanned = lb.scan_paths([root], [FAKE_TOKEN], root=root)
    assert (scanned["found"], scanned["complete"]) == (True, True)
    assert scanned["hits"] == [os.path.join(os.path.realpath(root), "logs", "mime-token.log")]
    detector = lb.Detector([FAKE_TOKEN])
    for chunk in (1, 2, 7, 64, 77, 97, len(blob)):
        reader = iter([blob[i : i + chunk] for i in range(0, len(blob), chunk)] + [b""])
        assert lb.scan_stream(lambda n: next(reader), detector, chunk=chunk)[0], f"missed at read size {chunk}"
    export = tmp_path / "export"
    with pytest.raises(lb.LeakFound):
        lb.export_logs(root, export, scanned["_files"], detector)
    assert not (export / "mime-token.log").exists(), "the export gate kept a copy holding the token"
    clean = base64.encodebytes(os.urandom(300))  # control: wrapped base64 with no token in it is not a hit
    assert not detector.hit(b"x\n" + clean)
