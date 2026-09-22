from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scripts import local_ci


@pytest.mark.parametrize("pg_failure", [False, True])
def test_run_isolates_dirty_source_and_continues_all_gates(monkeypatch, tmp_path: Path, pg_failure: bool) -> None:
    source = tmp_path / "source"
    source.mkdir()
    ci_home = tmp_path / "ci"
    monkeypatch.setenv("AGENT_LB_CI_HOME", str(ci_home))
    monkeypatch.setattr(local_ci, "repo_root", lambda: source)
    monkeypatch.setattr(local_ci, "resolve_ref", lambda *_: "a" * 40)
    monkeypatch.setattr(local_ci, "clean", lambda path: path != source)
    monkeypatch.setattr(local_ci, "worktree_at", lambda *_: True)
    targets = sorted(local_ci.BASELINE_TARGETS)
    monkeypatch.setattr(local_ci, "ci_targets", lambda *_: targets)
    environments: dict[str, dict[str, str]] = {}

    def fake_command(args, **kwargs):
        if args[:3] == ["git", "worktree", "add"]:
            Path(args[4]).mkdir(parents=True)
        return ""

    def fake_logged(args, cwd, env, log, timeout=1800):
        if args[0] == "make":
            assert cwd != source
            environments[args[1]] = env.copy()
        log.write_text("127.0.0.1:54399\n" if args[:2] == ["docker", "port"] else "test output\n")
        failed = pg_failure and args[:2] == ["docker", "run"]
        return {
            "command": args,
            "exit_code": 127 if failed else 0,
            "status": "failed" if failed else "passed",
            "timed_out": False,
            "duration_seconds": 0,
            "log_path": str(log),
            "log_sha256": local_ci.sha256(log),
        }

    monkeypatch.setattr(local_ci, "command", fake_command)
    monkeypatch.setattr(local_ci, "run_logged", fake_logged)
    assert local_ci.run("HEAD") == (1 if pg_failure else 0)
    path = local_ci.latest_receipt(ci_home, "a" * 40)
    assert path is not None
    data = json.loads(path.read_text())
    assert [leg["target"] for leg in data["legs"]] == targets
    assert set(environments) == set(targets) - (local_ci.PG_TARGETS if pg_failure else set())
    for target, env in environments.items():
        url = env["AGENT_LB_TEST_DATABASE_URL"]
        if target in local_ci.PG_TARGETS:
            assert url.startswith("postgresql+asyncpg://")
            assert url.endswith("/agent_lb_migration" if target == "migration-check-postgres" else "/agent_lb")
        else:
            assert url.startswith("sqlite+aiosqlite:///")
            assert "POSTGRES_TEST_DATABASE_URL" not in env
    assert local_ci.validate_receipt(path, "a" * 40)[0] is not pg_failure


def test_timeout_cannot_pass_when_child_handles_term_with_exit_zero(tmp_path: Path) -> None:
    entry = local_ci.run_logged(
        [sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, lambda *_: exit(0)); time.sleep(10)"],
        tmp_path,
        local_ci.os.environ.copy(),
        tmp_path / "timeout.log",
        timeout=1,
    )
    assert entry["exit_code"] == 0
    assert entry["timed_out"] is True
    assert entry["status"] == "failed"


def receipt(tmp_path: Path, *, sha: str = "a" * 40) -> Path:
    log = tmp_path / "gate.log"
    log.write_text("passed\n", encoding="utf-8")
    data = {
        "schema_version": local_ci.SCHEMA_VERSION,
        "exact_sha": sha,
        "passed": True,
        "runner": {"hostname": local_ci.socket.gethostname(), "user": local_ci.os.environ.get("USER", "unknown")},
        "required_targets": sorted(local_ci.BASELINE_TARGETS),
        "worktree_clean_before": True,
        "worktree_clean_after": True,
        "bootstrap": {
            "command": ["uv", "sync", "--dev", "--frozen", "--python", "3.13"],
            "exit_code": 0,
            "status": "passed",
            "timed_out": False,
            "duration_seconds": 1.0,
            "log_path": str(log),
            "log_sha256": local_ci.sha256(log),
        },
        "legs": [
            {
                "target": target,
                "command": ["make", target],
                "exit_code": 0,
                "status": "passed",
                "timed_out": False,
                "duration_seconds": 1.0,
                "log_path": str(log),
                "log_sha256": local_ci.sha256(log),
            }
            for target in sorted(local_ci.BASELINE_TARGETS)
        ],
        "postgres_setup": [],
        "cleanup": [],
    }
    for label in ("postgres-run", "postgres-port", "postgres-ready", "postgres-migration-createdb"):
        data["postgres_setup"].append(
            {
                "label": label,
                "command": ["setup", label],
                "exit_code": 0,
                "status": "passed",
                "timed_out": False,
                "duration_seconds": 1.0,
                "log_path": str(log),
                "log_sha256": local_ci.sha256(log),
            }
        )
    for label in ("postgres", "cluster", "image", "worktree"):
        data["cleanup"].append(
            {
                "label": label,
                "command": ["cleanup", label],
                "exit_code": 0,
                "status": "passed",
                "timed_out": False,
                "duration_seconds": 1.0,
                "log_path": str(log),
                "log_sha256": local_ci.sha256(log),
            }
        )
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_resolve_main_fetches_and_returns_exact_commit(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_command(args, **_kwargs):
        calls.append(args)
        return "b" * 40 if args[1] == "rev-parse" else ""

    monkeypatch.setattr(local_ci, "command", fake_command)
    assert local_ci.resolve_ref(tmp_path, "main") == "b" * 40
    assert calls == [["git", "fetch", "origin", "main"], ["git", "rev-parse", "--verify", "origin/main^{commit}"]]


def test_resolve_pr_uses_exact_head_sha(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_command(args, **_kwargs):
        calls.append(args)
        if args[0] == "gh":
            return "c" * 40
        if args[1] == "rev-parse":
            return "c" * 40
        return ""

    monkeypatch.setattr(local_ci, "command", fake_command)
    assert local_ci.resolve_ref(tmp_path, "123") == "c" * 40
    assert calls[0][:4] == ["gh", "pr", "view", "123"]
    assert calls[1] == ["git", "fetch", "origin", "c" * 40]


def test_ci_targets_rejects_empty_or_duplicate_output(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(local_ci, "command", lambda *_args, **_kwargs: "lint\nlint\n")
    try:
        local_ci.ci_targets(tmp_path)
    except RuntimeError as error:
        assert "target list" in str(error)
    else:
        raise AssertionError("duplicate target list accepted")


def test_receipt_validation_is_fail_closed_for_tamper_wrong_sha_machine_and_omitted_leg(tmp_path: Path) -> None:
    path = receipt(tmp_path)
    sha = "a" * 40
    assert local_ci.validate_receipt(path, sha)[0]
    data = json.loads(path.read_text(encoding="utf-8"))
    data["exact_sha"] = "b" * 40
    path.write_text(json.dumps(data), encoding="utf-8")
    assert not local_ci.validate_receipt(path, sha)[0]
    data["exact_sha"] = sha
    data["runner"]["hostname"] = "other-host"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert not local_ci.validate_receipt(path, sha)[0]
    data["runner"]["hostname"] = local_ci.socket.gethostname()
    data["legs"] = []
    path.write_text(json.dumps(data), encoding="utf-8")
    assert not local_ci.validate_receipt(path, sha)[0]


def test_receipt_validation_rejects_failed_prerequisite_and_changed_log(tmp_path: Path) -> None:
    path = receipt(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["bootstrap"]["status"] = "failed"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert not local_ci.validate_receipt(path, "a" * 40)[0]
    data["bootstrap"]["status"] = "passed"
    path.write_text(json.dumps(data), encoding="utf-8")
    Path(data["legs"][0]["log_path"]).write_text("modified", encoding="utf-8")
    assert not local_ci.validate_receipt(path, "a" * 40)[0]


def test_scrubbed_env_owns_artifacts_and_ignores_make_overrides(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PYTHON", "evil")
    monkeypatch.setenv("PYTEST_ARGS", "--ignore=tests")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k one_test --lf")
    monkeypatch.setenv("CI_HEAD_REF", "foreign")
    monkeypatch.setenv("KUBECONFIG", str(tmp_path / "foreign"))
    env = local_ci.scrubbed_env(tmp_path, "20260922T120000Z-ABC123")
    assert "PYTHON" not in env
    assert "PYTEST_ARGS" not in env
    assert "PYTEST_ADDOPTS" not in env
    assert "CI_HEAD_REF" not in env
    assert env["KUBECONFIG"] == str(tmp_path / "kubeconfig")
    assert env["CI_CLUSTER"] == "agent-lb-ci-20260922t120000z-abc123"
    assert env["CI_IMAGE"] == "ghcr.io/aneym/agent-lb-ci:20260922T120000Z-ABC123"


def test_status_rejects_noncanonical_sha_before_path_lookup(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LB_CI_HOME", str(tmp_path))
    assert local_ci.status("../" + "a" * 40) == 2
    assert local_ci.status("A" * 40) == 2


def test_validation_rejects_timeout_even_with_zero_exit(tmp_path: Path) -> None:
    path = receipt(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["legs"][0]["timed_out"] = True
    path.write_text(json.dumps(data), encoding="utf-8")
    assert not local_ci.validate_receipt(path, "a" * 40)[0]


def test_compute_passed_requires_setup_cleanup_and_no_runner_error(tmp_path: Path) -> None:
    path = receipt(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    targets = data["required_targets"]
    assert local_ci.compute_passed(data, targets)
    data["error"] = "interrupted"
    assert not local_ci.compute_passed(data, targets)
    del data["error"]
    data["cleanup"][0]["status"] = "failed"
    assert not local_ci.compute_passed(data, targets)
