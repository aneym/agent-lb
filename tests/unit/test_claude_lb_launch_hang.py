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


@pytest.mark.parametrize("registered", [True, False])
def test_shared_proxy_tracks_only_registered_client_activity(monkeypatch, tmp_path, registered):
    launcher = load_launcher_module()
    session_id = "client-session"
    monkeypatch.setattr(launcher, "proxy_ready_path", lambda session: tmp_path / f"{session}.proxy")
    activity = launcher.client_activity_path(session_id)
    assert activity is not None
    if registered:
        activity.touch()
    received = threading.Event()
    released = threading.Event()

    class StallingUpstream(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["content-length"]))
            received.set()
            released.wait(4)
            self.send_response(200)
            self.send_header("content-length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), StallingUpstream)
    upstream.daemon_threads = True
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    fake_server = SimpleNamespace(
        parent_pid=None, shared=True, session_id="shared", ccgpt_mode=False,
        upstream_base_url=f"http://127.0.0.1:{upstream.server_port}",
        activity=None, client_activity={}, client_activity_lock=threading.Lock(),
    )
    body = b'{}'
    request = (
        b"POST /v1/messages HTTP/1.1\r\nHost: api.anthropic.com\r\n"
        + f"Content-Length: {len(body)}\r\n".encode()
        + f"x-claude-code-session-id: {session_id}\r\n".encode()
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
    try:
        assert received.wait(2)
        if registered:
            assert activity.read_text().split()[0] == "1"
        else:
            assert not activity.exists()
    finally:
        released.set()
        handler_thread.join(timeout=2)
        client.close()
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=2)
    assert not handler_thread.is_alive()
    if registered:
        assert activity.read_text().split()[0] == "0"
    else:
        assert not activity.exists()
    assert fake_server.client_activity == {}


def test_client_activity_path_rejects_invalid_ids(monkeypatch, tmp_path):
    launcher = load_launcher_module()
    monkeypatch.setattr(launcher, "proxy_ready_path", lambda session: tmp_path / f"{session}.proxy")
    for session_id in ("../x", "a b", "a" * 129):
        assert launcher.client_activity_path(session_id) is None


def test_stale_client_activity_stops_supervised_session(monkeypatch, tmp_path, capsys):
    launcher = load_launcher_module()
    monkeypatch.setattr(launcher, "proxy_ready_path", lambda session: tmp_path / f"{session}.proxy")
    activity = launcher.client_activity_path("stale-session")
    assert activity is not None
    original_popen = subprocess.Popen

    def track_child(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        activity.write_text(f"1 {time.time() - 600}\n")
        return child

    monkeypatch.setattr(launcher.subprocess, "Popen", track_child)
    assert launcher.run_supervised(
        [sys.executable, "-c", "import time; time.sleep(60)"], tmp_path / "missing", 1.0,
        session_id="stale-session",
    ) == 75
    assert not activity.exists()
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 1
    assert "CLAUDE_LB_STALL_TIMEOUT" in lines[0]


def test_fresh_client_activity_keeps_supervised_session_alive(monkeypatch, tmp_path, capsys):
    launcher = load_launcher_module()
    monkeypatch.setattr(launcher, "proxy_ready_path", lambda session: tmp_path / f"{session}.proxy")
    activity = launcher.client_activity_path("streaming-session")
    assert activity is not None
    stopped = threading.Event()

    def write_progress():
        while not stopped.is_set():
            try:
                with activity.open("r+") as stream:
                    stream.seek(0)
                    stream.write(f"1 {time.time()}\n")
                    stream.truncate()
            except OSError:
                pass
            stopped.wait(0.3)

    writer = threading.Thread(target=write_progress, daemon=True)
    writer.start()
    try:
        assert launcher.run_supervised(
            [sys.executable, "-c", "import time; time.sleep(2.5)"], tmp_path / "missing", 1.0,
            session_id="streaming-session",
        ) == 0
    finally:
        stopped.set()
        writer.join(timeout=2)
    assert not activity.exists()
    assert not capsys.readouterr().err


@pytest.mark.parametrize("subagent", [False, True])
def test_transcript_writes_keep_supervised_session_alive(monkeypatch, tmp_path, capsys, subagent):
    launcher = load_launcher_module()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    slug = launcher.re.sub(r"[^A-Za-z0-9]", "-", os.getcwd())
    session_id = "transcript-test"
    transcript = tmp_path / "projects" / slug / (
        f"{session_id}/subagents/agent-x.jsonl" if subagent else f"{session_id}.jsonl"
    )
    script = (
        "import pathlib, sys, time\n"
        "path = pathlib.Path(sys.argv[1]); path.parent.mkdir(parents=True, exist_ok=True)\n"
        "for _ in range(12):\n"
        "    with path.open('a') as stream: stream.write('{}\\n')\n"
        "    time.sleep(.3)\n"
    )
    assert launcher.run_supervised(
        [sys.executable, "-c", script, str(transcript)], tmp_path / "missing", 1.0,
        session_id=session_id,
    ) == 0
    assert not capsys.readouterr().err


def test_late_work_process_keeps_idle_session_alive(monkeypatch, tmp_path, capsys):
    launcher = load_launcher_module()
    monkeypatch.setattr(launcher, "STALL_STARTUP_GRACE", 0.5)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    script = (
        "import subprocess, time\n"
        "time.sleep(1)\n"
        "subprocess.run(['sleep', '3'], check=True)\n"
    )
    assert launcher.run_supervised(
        [sys.executable, "-c", script], tmp_path / "missing", 1.0,
        session_id="idle-session",
    ) == 0
    assert not capsys.readouterr().err


def test_early_shell_tool_keeps_idle_session_alive(tmp_path, capsys):
    launcher = load_launcher_module()
    # A bare `sleep 3` is exec-optimized by macOS sh and leaves no shell to detect.
    script = "import subprocess; subprocess.run(['/bin/sh', '-c', 'sleep 3; :'], check=True)"

    assert launcher.run_supervised(
        [sys.executable, "-c", script], tmp_path / "missing", 1.0,
    ) == 0
    assert not capsys.readouterr().err


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (
            "/bin/zsh -c source /Users/aneyman/.claude/shell-snapshots/"
            "snapshot-zsh-....sh 2>/dev/null || true && python3 wait100.py",
            True,
        ),
        ("-zsh", False),
        ("node /x/mcp-server.js", False),
        (
            "/bin/bash -c source /home/jobs/.claude/shell-snapshots/"
            "snapshot-bash-1.sh 2>/dev/null || true && eval 'python3 w.py'",
            True,
        ),
        ("bash -lc 'python3 w.py'", True),
        ("bash -c -l 'python3 w.py'", True),
        ("bash -l -c x", True),
        ("-bash -c x", True),
        ("bash --login -c x", True),
        ("bash -o pipefail -c x", True),
        ("zsh -ic x", True),
        ("sh -c x", True),
        ("bash script.sh -c", False),
        ("bash -l", False),
        ("bash -- -c", False),
        ("python3 -c x", False),
        ("bash -o c", False),
        ("bash", False),
    ],
)
def test_shell_command_classifier(args, expected):
    launcher = load_launcher_module()
    assert launcher._shell_command_process(args) is expected


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
        lines = capsys.readouterr().err.splitlines()
        assert len(lines) == 1
        assert "CLAUDE_LB_STALL_TIMEOUT" in lines[0]
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.wait()


def test_early_mcp_like_child_does_not_keep_session_alive(monkeypatch, tmp_path, capsys):
    launcher = load_launcher_module()
    monkeypatch.setattr(launcher, "STALL_STARTUP_GRACE", 0.5)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    child = None
    original_popen = subprocess.Popen

    def track_child(*args, **kwargs):
        nonlocal child
        child = original_popen(*args, **kwargs)
        return child

    monkeypatch.setattr(launcher.subprocess, "Popen", track_child)
    pidfile = tmp_path / "grandchild.pid"
    script = (
        "import pathlib, subprocess, sys, time\n"
        "proc = subprocess.Popen(['sleep', '60'])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(proc.pid))\n"
        "time.sleep(60)\n"
    )
    try:
        started = time.monotonic()
        assert launcher.run_supervised(
            [sys.executable, "-c", script, str(pidfile)], tmp_path / "missing", 1.0,
            session_id="mcp-session",
        ) == 75
        assert time.monotonic() - started < 5
        assert child is not None and child.poll() is not None
        lines = capsys.readouterr().err.splitlines()
        assert len(lines) == 1
        assert "CLAUDE_LB_STALL_TIMEOUT" in lines[0]
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.wait()
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text()), 9)
            except ProcessLookupError:
                pass


def test_idle_activity_does_not_count_tool_execution(tmp_path, capsys):
    launcher = load_launcher_module()
    activity = tmp_path / "idle.activity"
    activity.write_text(f"0 {time.time() - 600}\n")

    assert launcher.run_supervised(
        [sys.executable, "-c", "import time; time.sleep(2.5)"], activity, 1.0,
    ) == 0
    assert not activity.exists()
    assert not capsys.readouterr().err


def test_main_no_auto_resume_passes_session_id_to_supervisor(monkeypatch, tmp_path):
    launcher = load_launcher_module()
    setup_main(monkeypatch, launcher, tmp_path)
    monkeypatch.setenv("CLAUDE_LB_AUTO_RESUME", "0")
    monkeypatch.setattr(launcher, "start_lb_proxy", lambda session: "http://127.0.0.1:12345")
    observed = []

    def capture(command, activity, timeout, session_id=None):
        observed.append((command, session_id))
        return 0

    monkeypatch.setattr(launcher, "run_supervised", capture)
    with pytest.raises(SystemExit) as exit_info:
        launcher.main()

    assert exit_info.value.code == 0
    assert len(observed) == 1
    command, session_id = observed[0]
    assert session_id is not None
    assert command[command.index("--session-id") + 1] == session_id


def test_stall_does_not_claim_route_for_resume(monkeypatch, tmp_path):
    launcher = load_launcher_module()
    monkeypatch.setattr(launcher, "proxy_ready_path", lambda session: tmp_path / f"{session}.proxy")
    monkeypatch.setattr(launcher, "run_supervised", lambda *args, **kwargs: 75)
    monkeypatch.setattr(
        launcher, "claim_session_route", lambda *args: pytest.fail("stall must not claim route"),
    )

    assert launcher.run_headless_with_resume(["claude", "-p", "hello"], ["-p", "hello"], "test", "opus", "weekly") == 75
