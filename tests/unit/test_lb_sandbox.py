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


def _key(root: Path) -> None:
    """The store key start creates: data/encryption.key, 0600, one link (_serve refuses to boot without it)."""
    (root / "data").mkdir(parents=True, exist_ok=True)
    key = root / "data" / "encryption.key"
    key.write_bytes(b"k" * 44)
    key.chmod(0o600)


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
    ("cmd", "owned"),
    [
        ("/h/.agent-lb/sandboxes/r1/runtime/.venv/bin/python -m app.cli --port 2471", True),
        ("node /h/.agent-lb/sandboxes/r1/runtime/scripts/agent-lb-front.mjs /h/.agent-lb/sandboxes/r1", True),
        ("python lb-restart --reap 9 210 --sandbox /h/.agent-lb/sandboxes/r1/lb-restart.json", True),
        ("/h/.agent-lb/sandboxes/r10/runtime/.venv/bin/python -m app.cli --port 2481", False),
        ("node agent-lb-front.mjs /h/.agent-lb/sandboxes/r1-b", False),
        ("/h/.agent-lb/runtime/agent-lb/.venv/bin/agent-lb --host 127.0.0.1 --port 2457", False),
    ],
)
def test_stop_only_claims_processes_of_its_own_root(cmd: str, owned: bool) -> None:
    lb = _load()
    assert lb.names_root(cmd, Path("/h/.agent-lb/sandboxes/r1")) is owned
    assert lb.names_run(cmd, "r1") is (owned and "r1" in cmd.split("/"))


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
    (root / "sandbox.json").write_text(json.dumps({"ports": SERVE_PORTS}))
    live_plist = tmp_path / "live.plist"
    live_plist.write_bytes(plistlib.dumps({"EnvironmentVariables": {"AGENT_LB_FEDERATION_TOKEN": FAKE_TOKEN}}))
    monkeypatch.setattr(lb, "SANDBOXES", sandboxes)
    monkeypatch.setattr(lb, "LIVE_PLIST", live_plist)
    monkeypatch.setenv("LB_SANDBOX_ROOT", str(root.resolve()))
    monkeypatch.setenv("AGENT_LB_DATA_DIR", str(root.resolve() / "data"))
    monkeypatch.delenv("AGENT_LB_FEDERATION_TOKEN", raising=False)
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
        "    (root / 'boot-report.json').write_text(json.dumps(report))\n"
        "    deadline = time.time() + 30\n"
        "    while time.time() < deadline and not (root / 'stop').exists():\n"
        "        time.sleep(0.05)\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    ),
}


def _fake_sandbox(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A sandbox root with the real lb-sandbox copy and a stand-in app; returns (sandboxes, root, live plist)."""
    sandboxes = tmp_path / "sandboxes"
    root = sandboxes / "r1"
    for sub in ("data", "bin", "runtime", "logs", "state", "home"):
        (root / sub).mkdir(parents=True)
    (root / "sandbox.json").write_text(json.dumps({"run_id": "r1", "ports": SERVE_PORTS, "transport": "http"}))
    _key(root)
    (root / "bin" / "lb-sandbox").write_bytes(SCRIPT.read_bytes())
    for rel, text in FAKE_APP.items():
        (root / "runtime" / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / "runtime" / rel).write_text(text)
    (root / "runtime" / ".venv").symlink_to(Path(sys.prefix), target_is_directory=True)
    live_plist = tmp_path / "live.plist"
    live_plist.write_bytes(plistlib.dumps({"EnvironmentVariables": {"AGENT_LB_FEDERATION_TOKEN": FAKE_TOKEN}}))
    return sandboxes, root.resolve(), live_plist


def _ps_env(pid: int) -> bytes:
    return subprocess.run(["/bin/ps", "-E", "-ww", "-o", "command=", "-p", str(pid)], capture_output=True).stdout


@pytest.mark.skipif(sys.platform != "darwin", reason="ps -E semantics are macOS")
def test_serve_hands_the_token_over_a_pipe_never_through_ps_eww(tmp_path: Path) -> None:
    """Integration: the real _serve execs the real _boot, which starts a stand-in app in the same process.

    `ps eww` (here `ps -E`) of that process must show its environment (the control) and never the token;
    the app's settings must hold it while os.environ, which children inherit, must not.
    """
    import hashlib
    import time

    sandboxes, root, live_plist = _fake_sandbox(tmp_path)
    driver = (
        "import importlib.machinery, importlib.util, sys\n"
        "from pathlib import Path\n"
        f"loader = importlib.machinery.SourceFileLoader('lbs', {str(SCRIPT)!r})\n"
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
    finally:
        (root / "stop").touch()
        proc.wait(30)
    assert report["pid"] == proc.pid, "the app must run in the exec'd process itself, no second exec"
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
    (root / "sandbox.json").write_text(json.dumps({"ports": SERVE_PORTS}))
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

    def __init__(self, lb: ModuleType, fault_file: Path, body: bytes, break_after: int | None) -> None:
        import asyncio
        import threading

        self.lb, self.fault_file, self.body, self.break_after = lb, fault_file, body, break_after
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
            response = web.StreamResponse(status=200, headers={"content-type": "text/event-stream"})
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
        counters = {"edge_requests": {"anthropic": 0}, "faults_applied": {"anthropic": 0}}
        handler = self.lb.make_edge_handler(
            "anthropic",
            f"http://127.0.0.1:{up_port}",
            self.lb.FaultState(self.fault_file),
            self.session,
            counters,
            self.entries.append,
        )
        edge = web.Application()
        edge.router.add_route("*", "/{tail:.*}", handler)
        return await self._serve(edge)

    async def _serve(self, app) -> int:
        from aiohttp import web

        runner = web.AppRunner(app, access_log=None, handle_signals=False)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        self.runners.append(runner)
        return site._server.sockets[0].getsockname()[1]

    def fetch(self, wait_before_reading: float) -> tuple[bytes, bool]:
        """POST through the edge; returns the body bytes received and whether the body ended cleanly."""
        import http.client
        import time

        conn = http.client.HTTPConnection("127.0.0.1", self.edge_port, timeout=30)
        conn.request("POST", "/v1/messages", body=b"{}", headers={"content-type": "application/json"})
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
    (root / "sandbox.json").write_text(json.dumps({"ports": ports}))
    monkeypatch.setattr(lb, "SANDBOXES", root.parent)
    monkeypatch.setenv("LB_SANDBOX_ROOT", str(root))
    with pytest.raises(lb.Refused):
        lb.serve_exec_args(root, ["--port", "2481"])


@pytest.mark.parametrize("relative", ["data", "data/store.db", "data/encryption.key"])
def test_serve_refuses_redirected_data_before_credentials(tmp_path, monkeypatch, relative):
    lb = _load()
    root = tmp_path / "sandboxes" / "r1"
    (root / "data").mkdir(parents=True)
    (root / "sandbox.json").write_text(json.dumps({"ports": SERVE_PORTS}))
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
    lb.create_key(key)
    assert (oct(key.stat().st_mode & 0o777), key.stat().st_nlink, len(key.read_bytes())) == ("0o600", 1, 44)
    with pytest.raises(FileExistsError):
        lb.create_key(key)  # never reuses a key someone placed there
    planted = tmp_path / "planted.key"
    (tmp_path / "link.key").symlink_to(planted)
    with pytest.raises(OSError):
        lb.create_key(tmp_path / "link.key")
    assert not planted.exists()


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


def _teardown_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lb: ModuleType) -> Path:
    sandboxes = tmp_path / "sandboxes"
    root = sandboxes / "lbsbx-unit-teardown"
    _keyed_store(lb, root)
    for sub in ("logs", "state", "home"):
        (root / sub).mkdir()
    (root / "logs" / "primary.log").write_text("INFO started\n")
    # Ports nothing listens on; labels that are never loaded: teardown has no live job to stop.
    (root / "sandbox.json").write_text(json.dumps({"run_id": root.name, "ports": {"front": 2597, "primary": 2598}}))
    live_plist = tmp_path / "live.plist"
    live_plist.write_bytes(plistlib.dumps({"EnvironmentVariables": {"AGENT_LB_FEDERATION_TOKEN": FAKE_TOKEN}}))
    monkeypatch.setattr(lb, "SANDBOXES", sandboxes.resolve())
    monkeypatch.setattr(lb, "LIVE_PLIST", live_plist)
    return root


@pytest.mark.skipif(sys.platform != "darwin", reason="teardown asks launchctl, which is macOS")
@pytest.mark.parametrize("planted", [None, "state/front.json", "home/.cache/blob", "logs/edge.jsonl"])
def test_teardown_scans_the_whole_root_and_refuses_export_on_a_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, planted: str | None
) -> None:
    """Integration over real files, ps, lsof and launchctl (for labels that were never loaded)."""
    lb = _load()
    root = _teardown_fixture(tmp_path, monkeypatch, lb)
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


def _held_fetch(port: int) -> tuple[object, object]:
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    body = json.dumps({"model": "claude-unit", "stream": True, "messages": []})
    conn.request("POST", "/v1/messages", body=body, headers={"content-type": "application/json"})
    return conn, conn.getresponse()


@pytest.mark.parametrize("release", ["clear", "timeout"])
def test_edge_holds_a_stream_open_until_released(tmp_path: Path, release: str) -> None:
    """Integration over HTTP: the restart check's stream stays in flight until the check releases it.

    A hold that ends by its bound is reported as "timeout", which the check counts as a failed restart proof.
    """
    import time

    lb = _load()
    fault_file = tmp_path / "fault-anthropic.json"
    bound = 30 if release == "clear" else 1
    fault_file.write_text(json.dumps({"nonce": "n1", "armed": True, "fault": "hold_stream", "n": bound}))
    rig = _EdgeRig(lb, fault_file, b"upstream must not be called", break_after=None)
    try:
        conn, resp = _held_fetch(rig.edge_port)
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


@pytest.mark.skipif(sys.platform != "darwin", reason="teardown asks launchctl, which is macOS")
def test_teardown_stops_a_process_that_names_only_the_run_and_never_reports_its_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding: a leftover naming the run id but not the root survived, and its raw command (which can hold
    a token) went into the JSON. It is stopped, and leftovers are reported by pid and hash only."""
    lb = _load()
    root = _teardown_fixture(tmp_path, monkeypatch, lb)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", root.name, FAKE_TOKEN])
    try:
        result = lb.teardown(root.name, None)
        stopped = proc.wait(10) is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert stopped and result["processes"] == []
    assert FAKE_TOKEN not in json.dumps(result)
    assert set(lb.command_ref("x " + FAKE_TOKEN)) <= set("0123456789abcdef")


@pytest.mark.skipif(sys.platform != "darwin", reason="teardown asks launchctl, which is macOS")
def test_teardown_never_unlinks_a_key_through_a_linked_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding: with <root>/data swapped for a link to the live data dir, stop deleted the live key."""
    import shutil as sh

    lb = _load()
    root = _teardown_fixture(tmp_path, monkeypatch, lb)
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
    monkeypatch.setattr(lb, "SANDBOXES", sandboxes)
    monkeypatch.setattr(lb, "check_launchctl", lambda: None)
    with pytest.raises(lb.Refused):
        lb.cmd_restart(argparse.Namespace(run_id="r1", reason="unit"))
    assert live.read_text() == "live plist"


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_client_env_never_truncates_a_planted_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    """Finding: client-env truncated clients/codex/config.toml through a planted link to a live file."""
    import argparse

    lb = _load()
    sandboxes = tmp_path / "sandboxes"
    root = sandboxes / "r1"
    (root / "clients" / "codex").mkdir(parents=True)
    (root / "sandbox.json").write_text(
        json.dumps({"run_id": "r1", "ports": SERVE_PORTS, "label": "com.agent-lb.drill.sbx-r1"})
    )
    live = tmp_path / "live-config.toml"
    live.write_text("live config")
    if kind == "symlink":
        (root / "clients" / "codex" / "config.toml").symlink_to(live)
    else:
        os.link(live, root / "clients" / "codex" / "config.toml")
    monkeypatch.setattr(lb, "SANDBOXES", sandboxes)
    lb.cmd_client_env(argparse.Namespace(run_id="r1", vendor="codex"))
    assert live.read_text() == "live config"
    assert "backend-api/codex" in (root / "clients" / "codex" / "config.toml").read_text()


@pytest.mark.parametrize("name", ["store.db", "encryption.key", "store.db-wal"])
def test_serve_refuses_a_store_or_key_hard_linked_to_live_custody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Finding: a hard link passed the symlink and resolve checks, so the sandbox opened the live DB."""
    lb = _load()
    sandboxes = tmp_path / "sandboxes"
    root = sandboxes / "r1"
    _key(root)
    (root / "sandbox.json").write_text(json.dumps({"ports": SERVE_PORTS}))
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
    with pytest.raises(lb.Refused):
        lb.serve_exec_args(root, ["--host", "127.0.0.1", "--port", "2482"])
    (root / "data" / name).unlink()
    if name == "encryption.key":
        _key(root)
    assert lb.serve_exec_args(root, ["--host", "127.0.0.1", "--port", "2482"])[3] == FAKE_TOKEN  # control
