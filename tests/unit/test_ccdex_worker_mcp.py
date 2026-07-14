from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = ROOT / "clients" / "ccdex-worker-mcp"


def load_module():
    loader = importlib.machinery.SourceFileLoader("ccdex_worker_mcp_unit", str(SERVER_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def rpc(server: subprocess.Popen[str], payload: dict[str, object]) -> dict[str, object]:
    assert server.stdin is not None and server.stdout is not None
    server.stdin.write(json.dumps(payload) + "\n")
    server.stdin.flush()
    line = server.stdout.readline()
    assert line
    return json.loads(line)


def test_mcp_initialize_tools_list_and_unknown_tool(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment["CCDEX_WORKER_HOME"] = str(tmp_path / "jobs")
    with subprocess.Popen(
        [sys.executable, str(SERVER_PATH)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    ) as server:
        initialized = rpc(
            server,
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "old-version"}},
        )
        assert initialized["result"]["serverInfo"]["name"] == "ccdex-worker-mcp"
        assert initialized["result"]["protocolVersion"] == "2025-06-18"
        listed = rpc(server, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = listed["result"]["tools"]
        assert {tool["name"] for tool in tools} == {
            "ccdex_start",
            "ccdex_status",
            "ccdex_result",
            "ccdex_reply",
            "ccdex_cancel",
            "ccdex_list",
        }
        assert all(tool["inputSchema"]["type"] == "object" for tool in tools)
        unknown = rpc(
            server,
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "missing", "arguments": {}}},
        )
        assert unknown["result"]["isError"] is True
        server.stdin.close()
        server.wait(timeout=5)


def test_read_only_and_write_worker_argv() -> None:
    module = load_module()
    base = {
        "worker_binary": "/tmp/ccdex",
        "turn": 1,
        "session_id": None,
    }
    read_only = module.build_worker_command({**base, "mode": "read-only"})
    assert read_only[:5] == ["/tmp/ccdex", "--print", "--input-format", "text", "--output-format"]
    assert read_only[read_only.index("--permission-mode") + 1] == "dontAsk"
    assert read_only[read_only.index("--tools") + 1] == "Read,Glob,Grep"
    assert "--safe-mode" in read_only
    assert "bypassPermissions" not in read_only

    write = module.build_worker_command({**base, "mode": "workspace-write"})
    assert write[write.index("--permission-mode") + 1] == "bypassPermissions"
    assert "--safe-mode" not in write


def test_reply_argv_uses_resume() -> None:
    module = load_module()
    command = module.build_worker_command(
        {
            "worker_binary": "/tmp/ccdex",
            "mode": "read-only",
            "turn": 2,
            "session_id": "11111111-2222-3333-4444-555555555555",
        }
    )
    assert command[command.index("--resume") + 1] == "11111111-2222-3333-4444-555555555555"


@pytest.mark.parametrize("prompt", ["--permission-mode=bypassPermissions", "--- diff"])
def test_prompt_text_is_never_in_worker_argv(prompt: str) -> None:
    module = load_module()
    command = module.build_worker_command(
        {"worker_binary": "/tmp/ccdex", "mode": "read-only", "turn": 1, "session_id": None}
    )
    assert prompt not in command
    assert command.count("--print") == 1
    assert command[command.index("--input-format") + 1] == "text"


def test_write_guard_and_input_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    monkeypatch.setenv("CCDEX_WORKER_HOME", str(tmp_path / "jobs"))
    monkeypatch.setattr(module, "resolve_worker_binary", lambda: "/tmp/stub")
    monkeypatch.setattr(module, "preflight_worker", lambda binary: None)
    with pytest.raises(module.WorkerError, match="workspace-write requires"):
        module.start_job(
            {
                "prompt": "edit",
                "cwd": str(tmp_path),
                "mode": "workspace-write",
                "isolation": "none",
            }
        )
    with pytest.raises(module.WorkerError, match="unknown start input"):
        module.start_job({"prompt": "x", "cwd": str(tmp_path), "surprise": True})
    with pytest.raises(module.WorkerError, match="cwd must be absolute"):
        module.start_job({"prompt": "x", "cwd": "."})
    with pytest.raises(module.WorkerError, match="base_ref must not begin"):
        module.start_job(
            {
                "prompt": "x",
                "cwd": str(tmp_path),
                "mode": "workspace-write",
                "isolation": "worktree",
                "base_ref": "--help",
            }
        )


def test_redaction_and_bounded_text() -> None:
    module = load_module()
    text = "api_key=supersecretvalue123 token sk-testabcdefghijklmnop " + ("x" * 100)
    result = module.bounded(text, 40)
    assert result is not None
    assert "supersecretvalue123" not in result
    assert "sk-testabcdefghijklmnop" not in result
    assert "[REDACTED]" in result
    assert "truncated" in result


def test_atomic_metadata_replacement_never_leaves_temp_files(tmp_path: Path) -> None:
    module = load_module()
    path = tmp_path / "metadata.json"
    for value in range(20):
        module.atomic_write_json(path, {"value": value})
        assert json.loads(path.read_text())["value"] == value
    assert list(tmp_path.glob(".metadata.json.*")) == []


def test_unchanged_metadata_update_does_not_rewrite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    job_dir = tmp_path / "job"
    module.atomic_write_json(job_dir / "metadata.json", {"state": "running", "updated_at": "unchanged"})
    writes: list[dict[str, object]] = []
    original = module.atomic_write_json

    def record_write(path: Path, payload: dict[str, object]) -> None:
        writes.append(payload)
        original(path, payload)

    monkeypatch.setattr(module, "atomic_write_json", record_write)
    result = module.update_metadata(job_dir, lambda payload: None)
    assert result["updated_at"] == "unchanged"
    assert writes == []


def test_malformed_rpc_and_unknown_job_are_errors() -> None:
    module = load_module()
    malformed = module.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": []})
    assert malformed["error"]["code"] == -32602
    unknown = module.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "ccdex_status", "arguments": {"job_id": "not-a-job"}},
        }
    )
    assert unknown["result"]["isError"] is True
    assert module.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_pid_pgid_mismatch_is_never_signalled(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(module.os, "getpgid", lambda pid: 700)
    monkeypatch.setattr(module.os, "killpg", lambda pgid, sig: signals.append((pgid, sig)))
    assert module.terminate_worker_identity(123, 701, grace_s=0) is False
    assert signals == []


def test_cancel_never_signals_supervisor_group_on_worker_identity_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_module()
    monkeypatch.setenv("CCDEX_WORKER_HOME", str(tmp_path))
    job_id = "00000000-0000-4000-8000-000000000013"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    payload = module.base_metadata(
        job_id,
        job_dir / "prompt",
        tmp_path,
        tmp_path,
        "read-only",
        "none",
        30,
        None,
        False,
        "/tmp/stub",
    )
    payload.update(
        state="running",
        supervisor_pid=os.getpid(),
        supervisor_pgid=os.getpgrp(),
        worker_pid=os.getpid(),
        worker_pgid=os.getpgrp(),
    )
    module.atomic_write_json(job_dir / "metadata.json", payload)
    monkeypatch.setattr(module, "recover_job", lambda _: module.read_metadata(job_dir))
    monkeypatch.setattr(module.os, "killpg", lambda *_: pytest.fail("must not signal supervisor group"))
    cancelled = module.cancel_job({"job_id": job_id})
    assert cancelled["state"] == "failed"
    assert "PID/PGID identity mismatch" in cancelled["error"]


def test_recovery_marks_worker_pid_pgid_mismatch_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    monkeypatch.setenv("CCDEX_WORKER_HOME", str(tmp_path))
    job_id = "00000000-0000-4000-8000-000000000016"
    job_dir = tmp_path / job_id
    job_dir.mkdir()
    payload = module.base_metadata(
        job_id,
        job_dir / "prompt",
        tmp_path,
        tmp_path,
        "read-only",
        "none",
        30,
        None,
        False,
        "/tmp/stub",
    )
    payload.update(
        state="running",
        supervisor_pid=456,
        supervisor_pgid=456,
        worker_pid=123,
        worker_pgid=701,
    )
    module.atomic_write_json(job_dir / "metadata.json", payload)
    monkeypatch.setattr(module.os, "getpgid", lambda pid: {123: 700, 456: 456}[pid])
    recovered = module.recover_job(job_dir)
    assert recovered["state"] == "failed"
    assert "PID/PGID identity mismatch" in recovered["error"]


def test_foreground_wait_has_timeout_plus_bounded_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    moments = iter([10.0, 10.0, 11.0, 16.0])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(moments))
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(module, "recover_job", lambda _: {"state": "running", "mode": "read-only"})
    monkeypatch.setattr(module, "result_payload", lambda payload: payload)
    result = module.wait_for_terminal("00000000-0000-4000-8000-000000000000", 1)
    assert result["state"] == "running"


def test_result_payload_omits_raw_logs_and_redacts_errors(tmp_path: Path) -> None:
    module = load_module()
    payload = {
        "job_id": "00000000-0000-4000-8000-000000000000",
        "state": "failed",
        "mode": "read-only",
        "isolation": "none",
        "final_text": "Bearer abcdefghijklmnop",
        "error": "password=hunter2-secret-value",
        "stdout_path": str(tmp_path / "stdout"),
        "stderr_path": str(tmp_path / "stderr"),
        "usage": {"input_tokens": 1},
    }
    result = module.result_payload(payload)
    assert result["final_text"] == "[REDACTED]"
    assert "hunter2" not in result["error"]
    assert "stdout" not in result and "stderr" not in result
    assert result["stdout_path"].endswith("stdout")
    assert result["stderr_path"].endswith("stderr")
