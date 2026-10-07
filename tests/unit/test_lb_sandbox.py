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
        ("label", "com.aneyman.agent-lb"),
        ("label", "com.agent-lb.drill."),
        ("label", "com.agent-lb.drill.x/y"),
        ("ports", [2455]),
        ("ports", [2457]),
        ("ports", [2459]),
        ("ports", [1455]),
        ("ports", [2469]),
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
    (root / "sandbox.json").write_text(json.dumps({"ports": {"primary": 2481, "standby": 2482}}))
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
    (root / "sandbox.json").write_text(json.dumps({"ports": {"primary": 2481, "standby": 2482}}))
    monkeypatch.setattr(lb, "SANDBOXES", sandboxes)
    monkeypatch.setenv("LB_SANDBOX_ROOT", str(root.resolve()))
    monkeypatch.setenv("AGENT_LB_DATA_DIR", str(root.resolve() / "data"))
    with pytest.raises(lb.Refused):
        lb.serve_exec_args(root, argv)
