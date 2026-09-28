from __future__ import annotations

import http.server
import importlib.machinery
import importlib.util
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


def load_launcher_module():
    path = Path(__file__).resolve().parents[2] / "clients" / "claude-lb-launch"
    loader = importlib.machinery.SourceFileLoader("claude_lb_launch_hang_test", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def setup_main(monkeypatch, launcher, tmp_path):
    monkeypatch.setattr(launcher.sys, "argv", ["claude-lb-launch", "-p", "say ok"])
    monkeypatch.setattr(launcher, "proxy_ready_path", lambda session: tmp_path / f"{session}.proxy")
    monkeypatch.setattr(launcher, "_prune_stale_shim_files", lambda *args: None)
    monkeypatch.setattr(launcher, "_spawn_lb_proxy", lambda *args: None)
    monkeypatch.setattr(launcher, "print_lb_banner", lambda *args: True)
    monkeypatch.setattr(launcher, "schedule_opus_doctor", lambda: None)
    monkeypatch.setattr(launcher, "_raise_fd_soft_limit", lambda *args: None)
    monkeypatch.setattr(launcher.os, "execvp", lambda *args: pytest.fail("must not exec claude"))
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("must not spawn claude"))
    monkeypatch.setattr(launcher.subprocess, "run", lambda *args, **kwargs: pytest.fail("must not run claude"))
    monkeypatch.setenv("CLAUDE_LB_SHIM_START_TIMEOUT", "0.2")
    monkeypatch.delenv("CLAUDE_LB_DISABLE", raising=False)
    launcher.CCGPT_MODE = False


def test_proxy_start_failure_is_one_retryable_line_without_fallback(monkeypatch, tmp_path, capsys):
    launcher = load_launcher_module()
    setup_main(monkeypatch, launcher, tmp_path)
    monkeypatch.delenv("CLAUDE_LB_ALLOW_FALLBACK", raising=False)

    with pytest.raises(SystemExit) as exit_info:
        launcher.main()

    assert exit_info.value.code == 75
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 1
    assert "0.2 seconds" in lines[0]
    assert "CLAUDE_LB_SHIM_START_TIMEOUT" in lines[0]
    assert "retryable" in lines[0]


def test_explicit_fallback_allows_plain_dry_run(monkeypatch, tmp_path, capsys):
    launcher = load_launcher_module()
    setup_main(monkeypatch, launcher, tmp_path)
    monkeypatch.setenv("CLAUDE_LB_ALLOW_FALLBACK", "1")
    monkeypatch.setenv("CLAUDE_LB_DRY_RUN", "1")

    launcher.main()

    assert capsys.readouterr().out.startswith("claude ")


def test_proxy_start_default_budget_is_90_seconds(monkeypatch, tmp_path):
    launcher = load_launcher_module()
    monkeypatch.setattr(launcher, "proxy_ready_path", lambda session: tmp_path / f"{session}.proxy")
    monkeypatch.setattr(launcher, "_prune_stale_shim_files", lambda *args: None)
    monkeypatch.setattr(launcher, "_spawn_lb_proxy", lambda *args: None)
    monkeypatch.delenv("CLAUDE_LB_SHIM_START_TIMEOUT", raising=False)
    clock = iter([100.0, 189.0, 190.0])
    monkeypatch.setattr(launcher.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(launcher.time, "sleep", lambda _: None)

    assert launcher.SHIM_START_TIMEOUT_DEFAULT == 90.0
    with pytest.raises(RuntimeError, match="did not start"):
        launcher.start_lb_proxy("sample")


def test_stalled_upstream_request_triggers_headless_stop(monkeypatch, tmp_path, capsys):
    launcher = load_launcher_module()
    launcher.CCGPT_MODE = False
    released = threading.Event()
    received = threading.Event()

    class StallingUpstream(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["content-length"]))
            received.set()
            released.wait(4)
            self.send_response(200)
            self.send_header("content-length", "2")
            self.end_headers()
            try:
                self.wfile.write(b"{}")
            except BrokenPipeError:
                pass

        def log_message(self, *args):
            pass

    upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), StallingUpstream)
    upstream.daemon_threads = True
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    activity = tmp_path / "sample.activity"
    fake_server = SimpleNamespace(
        parent_pid=os.getpid(), shared=False, session_id="sample", ccgpt_mode=False,
        upstream_base_url=f"http://127.0.0.1:{upstream.server_port}",
        activity=launcher._LbActivity(activity),
    )
    body = b'{"model":"claude-opus-4-8"}'
    request = (
        b"POST /v1/messages HTTP/1.1\r\nHost: api.anthropic.com\r\n"
        + f"Content-Length: {len(body)}\r\n".encode()
        + b"Connection: close\r\n\r\n" + body
    )
    client, server_socket = socket.socketpair()
    client.sendall(request)
    client.shutdown(socket.SHUT_WR)

    def forward():
        try:
            launcher._LbApiHandler(server_socket, ("127.0.0.1", 0), fake_server)
        finally:
            server_socket.close()

    handler_thread = threading.Thread(target=forward, daemon=True)
    handler_thread.start()
    child = None
    original_popen = subprocess.Popen

    def track_child(*args, **kwargs):
        nonlocal child
        child = original_popen(*args, **kwargs)
        return child

    monkeypatch.setattr(launcher.subprocess, "Popen", track_child)
    try:
        assert received.wait(2)
        assert activity.read_text().split()[0] == "1"
        started = time.monotonic()
        status = launcher.run_supervised(
            [sys.executable, "-c", "import time; time.sleep(60)"], activity, 1.0,
        )
        assert status == 75
        assert time.monotonic() - started < 5
        assert child is not None and child.poll() is not None
        lines = capsys.readouterr().err.splitlines()
        assert len(lines) == 1
        assert "CLAUDE_LB_STALL_TIMEOUT" in lines[0]
    finally:
        released.set()
        handler_thread.join(timeout=2)
        client.close()
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=2)
        if child is not None and child.poll() is None:
            child.kill()
            child.wait()


def test_missing_activity_stops_child(monkeypatch, tmp_path, capsys):
    launcher = load_launcher_module()
    child = None
    original_popen = subprocess.Popen

    def track_child(*args, **kwargs):
        nonlocal child
        child = original_popen(*args, **kwargs)
        return child

    monkeypatch.setattr(launcher.subprocess, "Popen", track_child)
    try:
        assert launcher.run_supervised(
            [sys.executable, "-c", "import time; time.sleep(60)"], tmp_path / "missing", 1.0,
        ) == 75
        assert child is not None and child.poll() is not None
        assert len(capsys.readouterr().err.splitlines()) == 1
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.wait()


def test_idle_activity_does_not_count_tool_execution(tmp_path, capsys):
    launcher = load_launcher_module()
    activity = tmp_path / "idle.activity"
    activity.write_text(f"0 {time.time() - 600}\n")

    assert launcher.run_supervised(
        [sys.executable, "-c", "import time; time.sleep(2.5)"], activity, 1.0,
    ) == 0
    assert not activity.exists()
    assert not capsys.readouterr().err


def test_stall_does_not_claim_route_for_resume(monkeypatch, tmp_path):
    launcher = load_launcher_module()
    monkeypatch.setattr(launcher, "proxy_ready_path", lambda session: tmp_path / f"{session}.proxy")
    monkeypatch.setattr(launcher, "run_supervised", lambda *args: 75)
    monkeypatch.setattr(
        launcher, "claim_session_route", lambda *args: pytest.fail("stall must not claim route"),
    )

    assert launcher.run_headless_with_resume(["claude", "-p", "hello"], ["-p", "hello"], "test", "opus", "weekly") == 75
