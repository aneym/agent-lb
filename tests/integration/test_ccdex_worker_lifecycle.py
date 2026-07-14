from __future__ import annotations

import concurrent.futures
import importlib.machinery
import importlib.util
import json
import os
import stat
import subprocess
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = ROOT / "clients" / "ccdex-worker-mcp"

STUB = r"""#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path

if "--help" in sys.argv:
    if os.environ.get("STUB_PREFLIGHT_FAIL") == "1":
        print("preflight rejected", file=sys.stderr)
        raise SystemExit(9)
    print("stub help")
    raise SystemExit(0)

prompt = sys.stdin.read()
record = os.environ.get("CCDEX_STUB_RECORD")
if record:
    invocation = {
        "argv": sys.argv[1:],
        "cwd": os.getcwd(),
        "lb_session": os.environ.get("CLAUDE_LB_SESSION_ID"),
        "prompt": prompt,
    }
    with Path(record).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(invocation) + "\n")
behavior = os.environ.get("STUB_BEHAVIOR", "success")
session = "11111111-2222-4333-8444-555555555555"
print(json.dumps({"type": "system", "subtype": "init", "session_id": session}), flush=True)
if behavior == "sleep":
    time.sleep(30)
elif behavior == "watchdog":
    for _ in range(3):
        print("Retrying request after transient error", file=sys.stderr, flush=True)
        time.sleep(0.05)
elif behavior == "fail":
    print("stub failure password=do-not-return-this", file=sys.stderr, flush=True)
    raise SystemExit(7)
elif behavior == "large":
    event = {
        "type": "result",
        "session_id": session,
        "result": "api_key=verysecretvalue123 " + "z" * 20000,
        "num_turns": 1,
        "usage": {"input_tokens": 3, "output_tokens": 4},
    }
    print(json.dumps(event), flush=True)
elif behavior == "assistant_retry":
    assistant = {
        "type": "assistant",
        "session_id": session,
        "message": {
            "content": [{"type": "text", "text": "will retry will retry will retry will retry"}],
            "usage": {"input_tokens": 2},
        },
    }
    result = {
        "type": "result",
        "session_id": session,
        "result": "will retry will retry will retry, then succeeded",
        "num_turns": 1,
        "usage": {"input_tokens": 2, "output_tokens": 3},
    }
    print(json.dumps(assistant), flush=True)
    print(json.dumps(result), flush=True)
else:
    if behavior == "delayed_success":
        time.sleep(0.5)
    assistant = {
        "type": "assistant",
        "session_id": session,
        "message": {
            "content": [{"type": "text", "text": "worker ok"}],
            "usage": {"input_tokens": 2},
        },
    }
    result = {
        "type": "result",
        "session_id": session,
        "result": "final worker ok",
        "num_turns": 1,
        "usage": {"input_tokens": 2, "output_tokens": 3},
    }
    print(json.dumps(assistant), flush=True)
    print(json.dumps(result), flush=True)
"""


def load_module(name: str):
    loader = importlib.machinery.SourceFileLoader(name, str(SERVER_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture
def worker_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[object, Path, Path]:
    stub = tmp_path / "stub-ccdex"
    stub.write_text(STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    home = tmp_path / "jobs"
    record = tmp_path / "argv.jsonl"
    monkeypatch.setenv("CCDEX_WORKER_HOME", str(home))
    monkeypatch.setenv("CCDEX_WORKER_BIN", str(stub))
    monkeypatch.setenv("CCDEX_STUB_RECORD", str(record))
    monkeypatch.delenv("STUB_BEHAVIOR", raising=False)
    monkeypatch.delenv("STUB_PREFLIGHT_FAIL", raising=False)
    return load_module(f"ccdex_worker_{time.monotonic_ns()}"), home, record


def wait_terminal(module, job_id: str, timeout: float = 10) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = module.recover_job(module.job_dir_for(job_id))
        if payload["state"] in module.TERMINAL_STATES:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not terminate")


def test_happy_lifecycle_status_result_reply_and_registry_reopen(worker_environment, tmp_path: Path) -> None:
    module, home, record = worker_environment
    started = module.start_job({"prompt": "inspect", "cwd": str(tmp_path), "background": True})
    job_id = started["job_id"]
    finished = wait_terminal(module, job_id)
    assert finished["state"] == "succeeded"
    assert finished["writer_lock"] is None
    assert finished["writer_lock_owner"] is None
    assert finished["session_id"] == "11111111-2222-4333-8444-555555555555"
    result = module.get_result({"job_id": job_id})
    assert result["final_text"] == "final worker ok"
    assert result["turns"] == 1
    assert result["usage"] == {"input_tokens": 2, "output_tokens": 3}
    assert Path(result["stdout_path"]).is_absolute()
    assert Path(result["stderr_path"]).is_absolute()

    reopened = load_module(f"ccdex_worker_reopened_{time.monotonic_ns()}")
    assert reopened.status_job({"job_id": job_id})["state"] == "succeeded"
    reply = reopened.reply_job({"job_id": job_id, "prompt": "continue", "background": False})
    assert reply["state"] == "succeeded"
    calls = [json.loads(line) for line in record.read_text().splitlines()]
    assert calls[0]["lb_session"] == job_id
    assert calls[0]["prompt"] == "inspect"
    assert "inspect" not in calls[0]["argv"]
    assert "--resume" in calls[1]["argv"]
    assert calls[1]["argv"][calls[1]["argv"].index("--resume") + 1] == finished["session_id"]
    assert reopened.list_jobs({"limit": 1})["jobs"][0]["job_id"] == job_id
    assert home.joinpath(job_id, "metadata.json").is_file()


def test_foreground_start_waits_for_result(worker_environment, tmp_path: Path) -> None:
    module, _, _ = worker_environment
    result = module.start_job({"prompt": "inspect", "cwd": str(tmp_path), "background": False})
    assert result["state"] == "succeeded"
    assert result["final_text"] == "final worker ok"


def test_foreground_request_does_not_block_independent_rpc(
    worker_environment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, _ = worker_environment
    monkeypatch.setenv("STUB_BEHAVIOR", "delayed_success")
    with subprocess.Popen(
        [str(SERVER_PATH)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ.copy(),
    ) as server:
        assert server.stdin is not None and server.stdout is not None
        start_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ccdex_start",
                "arguments": {"prompt": "slow", "cwd": str(tmp_path), "background": False},
            },
        }
        server.stdin.write(json.dumps(start_request) + "\n")
        server.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}) + "\n")
        server.stdin.flush()
        first = json.loads(server.stdout.readline())
        second = json.loads(server.stdout.readline())
        assert first["id"] == 2
        assert second["id"] == 1
        server.stdin.close()
        server.wait(timeout=5)


def test_timeout_marks_terminal_and_kills_group(
    worker_environment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, _, _ = worker_environment
    monkeypatch.setenv("STUB_BEHAVIOR", "sleep")
    started = module.start_job({"prompt": "wait", "cwd": str(tmp_path), "timeout_s": 1})
    finished = wait_terminal(module, started["job_id"])
    assert finished["state"] == "timeout"
    assert not module.process_alive(finished["worker_pid"])


def test_cancel_marks_terminal_and_kills_group(
    worker_environment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, _, _ = worker_environment
    monkeypatch.setenv("STUB_BEHAVIOR", "sleep")
    started = module.start_job({"prompt": "wait", "cwd": str(tmp_path)})
    job_id = started["job_id"]
    deadline = time.monotonic() + 5
    while module.status_job({"job_id": job_id})["state"] != "running" and time.monotonic() < deadline:
        time.sleep(0.05)
    cancelled = module.cancel_job({"job_id": job_id})
    assert cancelled["state"] == "cancelled"
    deadline = time.monotonic() + 5
    metadata = module.read_metadata(module.job_dir_for(job_id))
    while metadata["duration_s"] is None and time.monotonic() < deadline:
        time.sleep(0.05)
        metadata = module.read_metadata(module.job_dir_for(job_id))
    assert not module.process_alive(metadata["worker_pid"])
    assert metadata["duration_s"] is not None
    assert metadata["total_duration_s"] >= metadata["duration_s"]
    with pytest.raises(module.WorkerError, match="cancelled job"):
        module.reply_job({"job_id": job_id, "prompt": "should fail"})


def test_watchdog_kills_third_retry_signature(
    worker_environment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, _, _ = worker_environment
    monkeypatch.setenv("STUB_BEHAVIOR", "watchdog")
    started = module.start_job({"prompt": "retry", "cwd": str(tmp_path)})
    finished = wait_terminal(module, started["job_id"])
    assert finished["state"] == "watchdog_killed"


def test_watchdog_ignores_retry_language_in_assistant_json(
    worker_environment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, _, _ = worker_environment
    monkeypatch.setenv("STUB_BEHAVIOR", "assistant_retry")
    started = module.start_job({"prompt": "discuss retries", "cwd": str(tmp_path)})
    finished = wait_terminal(module, started["job_id"])
    assert finished["state"] == "succeeded"
    assert "then succeeded" in finished["final_text"]


@pytest.mark.parametrize("prompt", ["--permission-mode=bypassPermissions", "--- diff"])
def test_prompt_is_delivered_only_on_stdin(worker_environment, tmp_path: Path, prompt: str) -> None:
    module, _, record = worker_environment
    started = module.start_job({"prompt": prompt, "cwd": str(tmp_path)})
    finished = wait_terminal(module, started["job_id"])
    assert finished["state"] == "succeeded"
    invocation = json.loads(record.read_text().splitlines()[-1])
    assert invocation["prompt"] == prompt
    assert prompt not in invocation["argv"]
    assert invocation["argv"][invocation["argv"].index("--input-format") + 1] == "text"


def test_missing_binary_and_preflight_failure_fail_closed(
    worker_environment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, _, _ = worker_environment
    monkeypatch.setenv("CCDEX_WORKER_BIN", str(tmp_path / "missing"))
    with pytest.raises(module.WorkerError, match="missing or not executable"):
        module.start_job({"prompt": "x", "cwd": str(tmp_path)})

    stub = tmp_path / "bad-preflight"
    stub.write_text("#!/bin/sh\nexit 6\n")
    stub.chmod(0o700)
    monkeypatch.setenv("CCDEX_WORKER_BIN", str(stub))
    with pytest.raises(module.WorkerError, match="preflight failed"):
        module.start_job({"prompt": "x", "cwd": str(tmp_path)})


def test_bounded_redacted_result(worker_environment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module, _, _ = worker_environment
    monkeypatch.setenv("STUB_BEHAVIOR", "large")
    started = module.start_job({"prompt": "large", "cwd": str(tmp_path)})
    finished = wait_terminal(module, started["job_id"])
    result = module.result_payload(finished)
    assert "verysecretvalue123" not in result["final_text"]
    assert "[REDACTED]" in result["final_text"]
    assert "truncated" in result["final_text"]
    assert "stdout" not in result and "stderr" not in result


def init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "stub@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Stub"], check=True)
    path.joinpath("tracked.txt").write_text("base\n")
    subprocess.run(["git", "-C", str(path), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "base"], check=True)


def test_worktree_isolation_and_writer_lock(
    worker_environment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, _, record = worker_environment
    non_git = tmp_path / "plain"
    non_git.mkdir()
    with pytest.raises(module.WorkerError, match="git"):
        module.start_job({"prompt": "write", "cwd": str(non_git), "mode": "workspace-write", "isolation": "worktree"})

    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    base_sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    repo.joinpath("tracked.txt").write_text("second\n")
    subprocess.run(["git", "-C", str(repo), "commit", "-qam", "second"], check=True)
    with pytest.raises(module.WorkerError, match="base_ref must not begin"):
        module.start_job(
            {
                "prompt": "write",
                "cwd": str(repo),
                "mode": "workspace-write",
                "isolation": "worktree",
                "base_ref": "--help",
            }
        )
    isolated = module.start_job(
        {
            "prompt": "write",
            "cwd": str(repo),
            "mode": "workspace-write",
            "isolation": "worktree",
            "base_ref": base_sha,
        }
    )
    isolated_meta = wait_terminal(module, isolated["job_id"])
    assert isolated_meta["state"] == "succeeded"
    assert Path(isolated_meta["worktree_path"]).is_dir()
    assert isolated_meta["worktree_owned"] is True
    assert Path(isolated_meta["worktree_path"]).is_relative_to(module.job_dir_for(isolated["job_id"]))
    worktree_sha = subprocess.run(
        ["git", "-C", isolated_meta["worktree_path"], "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert worktree_sha == base_sha
    assert json.loads(record.read_text().splitlines()[-1])["cwd"] == isolated_meta["execution_cwd"]

    monkeypatch.setenv("STUB_BEHAVIOR", "sleep")
    isolated_reply = module.reply_job({"job_id": isolated["job_id"], "prompt": "continue"})
    deadline = time.monotonic() + 5
    while module.status_job({"job_id": isolated["job_id"]})["state"] != "running" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert isolated_reply["state"] in {"queued", "running"}
    with pytest.raises(module.WorkerError, match="active writer"):
        module.acquire_writer_lock(
            Path(isolated_meta["execution_cwd"]).resolve(),
            "00000000-0000-4000-8000-000000000004",
        )
    module.cancel_job({"job_id": isolated["job_id"]})

    monkeypatch.setenv("STUB_BEHAVIOR", "sleep")
    first = module.start_job(
        {
            "prompt": "write",
            "cwd": str(repo),
            "mode": "workspace-write",
            "isolation": "none",
            "allow_in_place": True,
        }
    )
    with pytest.raises(module.WorkerError, match="active writer"):
        module.start_job(
            {
                "prompt": "write again",
                "cwd": str(repo),
                "mode": "workspace-write",
                "isolation": "none",
                "allow_in_place": True,
            }
        )
    module.cancel_job({"job_id": first["job_id"]})

    stale_job = "00000000-0000-4000-8000-000000000002"
    stale_dir = module.job_dir_for(stale_job)
    stale_dir.mkdir()
    stale_payload = module.base_metadata(
        stale_job,
        stale_dir / "prompt",
        repo,
        repo,
        "workspace-write",
        "none",
        30,
        None,
        True,
        "/tmp/stub",
    )
    stale_lock = module.lock_path_for(repo.resolve())
    stale_lock.parent.mkdir(parents=True, exist_ok=True)
    stale_owner = {
        "job_id": stale_job,
        "turn": 1,
        "token": "1" * 32,
        "realpath": str(repo.resolve()),
    }
    stale_payload.update(
        state="running",
        supervisor_pid=999_999_981,
        supervisor_pgid=999_999_981,
        worker_pid=999_999_982,
        worker_pgid=999_999_982,
        writer_lock=str(stale_lock),
        writer_lock_owner=stale_owner,
    )
    module.atomic_write_json(stale_dir / "metadata.json", stale_payload)
    module.create_writer_lockfile(stale_lock, stale_owner)
    replacement = module.acquire_writer_lock(repo.resolve(), "00000000-0000-4000-8000-000000000003")
    assert replacement["job_id"] == "00000000-0000-4000-8000-000000000003"


def test_concurrent_writer_acquire_has_one_exact_owner(worker_environment, tmp_path: Path) -> None:
    module, _, _ = worker_environment
    realpath = tmp_path.resolve()
    barrier = threading.Barrier(2)
    job_ids = [
        "00000000-0000-4000-8000-000000000010",
        "00000000-0000-4000-8000-000000000011",
    ]

    def acquire(job_id: str):
        barrier.wait()
        try:
            return module.acquire_writer_lock(realpath, job_id)
        except module.WorkerError as error:
            return error

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(acquire, job_ids))
    owners = [result for result in results if isinstance(result, dict)]
    errors = [result for result in results if isinstance(result, module.WorkerError)]
    assert len(owners) == 1
    assert len(errors) == 1
    lock_path = module.lock_path_for(realpath)
    assert module.read_writer_owner(lock_path) == owners[0]
    wrong_owner = {**owners[0], "token": "f" * 32}
    module.release_writer_lock({"writer_lock": str(lock_path), "writer_lock_owner": wrong_owner})
    assert lock_path.exists()
    module.release_writer_lock({"writer_lock": str(lock_path), "writer_lock_owner": owners[0]})
    assert not lock_path.exists()


def test_missing_or_malformed_writer_owner_is_contended(worker_environment, tmp_path: Path) -> None:
    module, _, _ = worker_environment
    lock_path = module.lock_path_for(tmp_path.resolve())
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch()
    with pytest.raises(module.WorkerError, match="active writer"):
        module.acquire_writer_lock(tmp_path.resolve(), "00000000-0000-4000-8000-000000000014")
    lock_path.write_text("{}\n")
    with pytest.raises(module.WorkerError, match="active writer"):
        module.acquire_writer_lock(tmp_path.resolve(), "00000000-0000-4000-8000-000000000015")


def test_concurrent_reply_cannot_steal_writer_ownership(
    worker_environment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, home, _ = worker_environment
    job_id = "00000000-0000-4000-8000-000000000012"
    job_dir = home / job_id
    job_dir.mkdir(parents=True)
    payload = module.base_metadata(
        job_id,
        job_dir / "turn-1.prompt",
        tmp_path,
        tmp_path,
        "workspace-write",
        "none",
        30,
        None,
        True,
        os.environ["CCDEX_WORKER_BIN"],
    )
    payload.update(
        state="succeeded",
        session_id="11111111-2222-4333-8444-555555555555",
        turns=1,
    )
    module.atomic_write_json(job_dir / "metadata.json", payload)
    barrier = threading.Barrier(2)
    monkeypatch.setattr(module, "preflight_worker", lambda _: barrier.wait())
    monkeypatch.setattr(module, "launch_supervisor", lambda _: 123)

    def reply(prompt: str):
        try:
            return module.reply_job({"job_id": job_id, "prompt": prompt})
        except module.WorkerError as error:
            return error

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reply, ["one", "two"]))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, module.WorkerError) for result in results) == 1
    queued = module.read_metadata(job_dir)
    assert queued["state"] == "queued"
    assert module.read_writer_owner(Path(queued["writer_lock"])) == queued["writer_lock_owner"]
    module.release_writer_lock(queued)


def test_queued_job_with_live_starting_pid_is_not_recovered(worker_environment, tmp_path: Path) -> None:
    module, home, _ = worker_environment
    job_id = "00000000-0000-4000-8000-000000000005"
    job_dir = home / job_id
    job_dir.mkdir(parents=True)
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
    payload["supervisor_pid"] = os.getpid()
    payload["supervisor_pgid"] = os.getpgrp()
    module.atomic_write_json(job_dir / "metadata.json", payload)
    assert module.recover_job(job_dir)["state"] == "queued"


def test_orphan_recovery_marks_failed(worker_environment, tmp_path: Path) -> None:
    module, home, _ = worker_environment
    job_id = "00000000-0000-4000-8000-000000000001"
    job_dir = home / job_id
    job_dir.mkdir(parents=True)
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
    payload["state"] = "running"
    payload["supervisor_pid"] = 999_999_991
    payload["supervisor_pgid"] = 999_999_991
    payload["worker_pid"] = 999_999_992
    payload["worker_pgid"] = 999_999_992
    module.atomic_write_json(job_dir / "metadata.json", payload)
    recovered = module.recover_job(job_dir)
    assert recovered["state"] == "failed"
    assert "orphan recovery" in recovered["error"]
