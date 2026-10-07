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


def test_serve_puts_the_token_only_in_the_exec_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lb = _load()
    sandboxes = tmp_path / "sandboxes"
    root = sandboxes / "r1"
    (root / "data").mkdir(parents=True)
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

    python, argv, env = lb.serve_exec_args(root, ["--host", "127.0.0.1", "--port", "2482"])

    # Booleans only: a failing comparison must never print a token value.
    token_in_env = env.get("AGENT_LB_FEDERATION_TOKEN") == FAKE_TOKEN
    assert token_in_env, "the exec env does not carry the (fake) federation token"
    assert "AGENT_LB_FEDERATION_TOKEN" not in os.environ
    token_in_argv = any(FAKE_TOKEN in arg for arg in [python, *argv])
    assert not token_in_argv, "the token reached argv"
    assert argv[argv.index("--port") + 1] == "2482"
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before
    written = [
        p for p in tmp_path.rglob("*") if p.is_file() and p != live_plist and FAKE_TOKEN.encode() in p.read_bytes()
    ]
    assert written == []


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
    root.mkdir(parents=True)
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
    copied = lb.export_logs(logs, dest, scanned["_files"])
    assert (scanned["found"], scanned["complete"], copied) == (False, True, 1)
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
        lb.export_logs(logs, dest, [str(logs / "primary.log")])
    assert target.read_text() == "live sentinel"
