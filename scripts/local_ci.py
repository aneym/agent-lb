#!/usr/bin/env python3
"""Run the repository's complete CI gate locally in an isolated worktree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
PG_TARGETS = {"test-postgres", "migration-check-postgres"}
IMAGE_TARGETS = {"docker", "helm-smoke-kind"}
REQUIRED_TOOLS = (
    "git",
    "uv",
    "bun",
    "docker",
    "helm",
    "kubeconform",
    "kind",
    "kubectl",
    "trivy",
    "swift",
    "make",
    "codesign",
    "plutil",
)
BASELINE_TARGETS = {
    "contributors",
    "beta-release-guard",
    "frontend-lint",
    "frontend-typecheck",
    "frontend-test",
    "frontend-build",
    "lint",
    "typecheck",
    "test-unit",
    "test-integration-core",
    "test-integration-bridge",
    "test-e2e",
    "test-postgres",
    "migration-check",
    "migration-check-postgres",
    "package",
    "docker",
    "helm-check",
    "helm-smoke-kind",
    "menubar-test",
    "menubar-build",
}
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")


def now() -> str:
    return datetime.now(UTC).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def valid_sha(value: str) -> bool:
    return bool(SHA_RE.fullmatch(value))


def command(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    timeout: int = 300,
) -> str:
    result = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if check and result.returncode:
        raise RuntimeError(f"{' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def repo_root() -> Path:
    return Path(command(["git", "rev-parse", "--show-toplevel"]))


def resolve_ref(root: Path, ref: str) -> str:
    if ref == "main":
        command(["git", "fetch", "origin", "main"], cwd=root)
        ref = "origin/main"
    elif ref.isdigit():
        ref = command(["gh", "pr", "view", ref, "--json", "headRefOid", "-q", ".headRefOid"], cwd=root)
        command(["git", "fetch", "origin", ref], cwd=root)
    exact_sha = command(["git", "rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=root)
    if not valid_sha(exact_sha):
        raise RuntimeError("resolved ref is not an exact 40-character commit SHA")
    return exact_sha


def pr_event(root: Path, number: str, event_path: Path) -> None:
    repository = command(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"], cwd=root)
    payload = json.loads(command(["gh", "api", f"repos/{repository}/pulls/{number}"], cwd=root))
    event_path.write_text(json.dumps({"pull_request": payload}, indent=2) + "\n", encoding="utf-8")


def ci_targets(worktree: Path, env: dict[str, str] | None = None) -> list[str]:
    output = command(["make", "-s", "ci-targets"], cwd=worktree, env=env)
    targets = [line.strip() for line in output.splitlines() if line.strip()]
    if not targets or len(targets) != len(set(targets)) or set(targets) != BASELINE_TARGETS:
        raise RuntimeError("make ci-targets must emit the exact, nonempty, duplicate-free gate target list")
    return targets


def clean(worktree: Path) -> bool:
    return not command(["git", "status", "--porcelain"], cwd=worktree)


def worktree_at(worktree: Path, sha: str) -> bool:
    return command(["git", "rev-parse", "HEAD"], cwd=worktree) == sha


def scrubbed_env(run_dir: Path, run_id: str) -> dict[str, str]:
    rejected = {
        "DATABASE_URL",
        "POSTGRES_TEST_DATABASE_URL",
        "PYTHON",
        "PYTEST_ARGS",
        "PYTHONPATH",
        "PYTHONHOME",
        "UV_PROJECT_ENVIRONMENT",
        "VIRTUAL_ENV",
        "MAKEFLAGS",
        "MAKEFILES",
        "MAKEOVERRIDES",
        "CI_IMAGE",
        "CI_CLUSTER",
        "KUBECONFIG",
    }
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("AGENT_LB_", "PYTEST_", "CI_")) and key not in rejected
    }
    tool_dir = Path.home() / "agent-lb-ci" / "tools"
    if tool_dir.is_dir():
        env["PATH"] = f"{tool_dir}{os.pathsep}{env.get('PATH', '')}"
    env.update(
        {
            "AGENT_LB_DATA_DIR": str(run_dir / "data"),
            "AGENT_LB_TEST_DATABASE_URL": f"sqlite+aiosqlite:///{run_dir / 'test.db'}",
            "CI_IMAGE": f"ghcr.io/aneym/agent-lb-ci:{run_id}",
            "CI_CLUSTER": f"agent-lb-ci-{run_id}".lower(),
            "KUBECONFIG": str(run_dir / "kubeconfig"),
        }
    )
    return env


def run_logged(args: list[str], cwd: Path, env: dict[str, str], log: Path, timeout: int = 1800) -> dict[str, Any]:
    started = time.monotonic()
    timed_out = False
    exit_code = 127
    error: str | None = None
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as output:
        try:
            process = subprocess.Popen(
                args,
                cwd=cwd,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            error = f"{type(exc).__name__}: {exc}"
            output.write(error + "\n")
        else:
            try:
                exit_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    exit_code = process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    exit_code = process.wait()
            except BaseException:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                raise
    entry: dict[str, Any] = {
        "command": args,
        "exit_code": exit_code,
        "status": "passed" if exit_code == 0 and not timed_out else "failed",
        "timed_out": timed_out,
        "duration_seconds": round(time.monotonic() - started, 3),
        "log_path": str(log),
        "log_sha256": sha256(log),
    }
    if error:
        entry["error"] = error
    return entry


def blocked_leg(target: str, log: Path, reason: str) -> dict[str, Any]:
    log.write_text(reason + "\n", encoding="utf-8")
    return {
        "target": target,
        "command": ["make", target],
        "exit_code": 125,
        "status": "failed",
        "timed_out": False,
        "duration_seconds": 0.0,
        "log_path": str(log),
        "log_sha256": sha256(log),
        "not_run_reason": reason,
    }


def successful(entry: Any) -> bool:
    return (
        isinstance(entry, dict)
        and entry.get("status") == "passed"
        and entry.get("exit_code") == 0
        and entry.get("timed_out") is False
    )


def write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def record_command(
    receipt: dict[str, Any],
    receipt_path: Path,
    section: str,
    label: str,
    args: list[str],
    cwd: Path,
    env: dict[str, str],
    log: Path,
    timeout: int,
) -> dict[str, Any]:
    print(f"[{section}] START {label}: {' '.join(args)}", flush=True)
    entry = run_logged(args, cwd, env, log, timeout)
    entry["label"] = label
    receipt[section].append(entry)
    write_receipt(receipt_path, receipt)
    outcome = (
        f"exit={entry['exit_code']} timeout={entry['timed_out']} "
        f"duration={entry['duration_seconds']}s"
    )
    print(f"[{section}] {entry['status'].upper()} {label} {outcome}", flush=True)
    return entry


def expected_labels(targets: list[str]) -> tuple[set[str], set[str]]:
    setup = {"postgres-run", "postgres-port", "postgres-ready"} if PG_TARGETS & set(targets) else set()
    if "migration-check-postgres" in targets:
        setup.add("postgres-migration-createdb")
    cleanup = {"worktree"}
    if PG_TARGETS & set(targets):
        cleanup.add("postgres")
    if "helm-smoke-kind" in targets:
        cleanup.add("cluster")
    if IMAGE_TARGETS & set(targets):
        cleanup.add("image")
    return setup, cleanup


def compute_passed(receipt: dict[str, Any], targets: list[str]) -> bool:
    expected_setup, expected_cleanup = expected_labels(targets)
    setup = receipt.get("postgres_setup", [])
    cleanup = receipt.get("cleanup", [])
    return bool(
        targets
        and not receipt.get("error")
        and receipt.get("worktree_clean_before")
        and receipt.get("worktree_clean_after")
        and successful(receipt.get("bootstrap"))
        and [entry.get("target") for entry in receipt.get("legs", [])] == targets
        and all(successful(entry) for entry in receipt.get("legs", []))
        and {entry.get("label") for entry in setup} == expected_setup
        and all(successful(entry) for entry in setup)
        and {entry.get("label") for entry in cleanup} == expected_cleanup
        and all(successful(entry) for entry in cleanup)
    )


def run(ref: str) -> int:
    root = repo_root()
    exact_sha = resolve_ref(root, ref)
    run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    home = Path(os.environ.get("AGENT_LB_CI_HOME", str(Path.home() / "agent-lb-ci"))).expanduser()
    run_dir = home / "runs" / exact_sha / run_id
    receipt_path = home / "receipts" / exact_sha / run_id / "receipt.json"
    worktree, logs = run_dir / "worktree", run_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    env = scrubbed_env(run_dir, run_id)
    (run_dir / "data").mkdir()
    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "exact_sha": exact_sha,
        "run_id": run_id,
        "runner": {"hostname": socket.gethostname(), "user": os.environ.get("USER", "unknown")},
        "started_at": now(),
        "required_targets": [],
        "bootstrap": None,
        "legs": [],
        "postgres_setup": [],
        "cleanup": [],
        "worktree_clean_before": False,
        "worktree_clean_after": False,
        "passed": False,
    }
    targets: list[str] = []
    pg_name = f"agent-lb-local-ci-pg-{run_id}"
    pg_url: str | None = None
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def interrupt_on_sigterm(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt("received SIGTERM")

    signal.signal(signal.SIGTERM, interrupt_on_sigterm)
    write_receipt(receipt_path, receipt)
    print(f"[receipt] {receipt_path}", flush=True)
    try:
        command(["git", "worktree", "add", "--detach", str(worktree), exact_sha], cwd=root)
        receipt["worktree_clean_before"] = clean(worktree) and worktree_at(worktree, exact_sha)
        targets = ci_targets(worktree, env)
        receipt["required_targets"] = targets
        env["CI_BASE_REF"] = "origin/main"
        if ref.isdigit():
            event_path = run_dir / "pr-event.json"
            pr_event(root, ref, event_path)
            event = json.loads(event_path.read_text(encoding="utf-8"))["pull_request"]
            env["CI_EVENT_PATH"] = str(event_path)
            env["CI_HEAD_REF"] = str(event.get("head", {}).get("ref", ""))
        write_receipt(receipt_path, receipt)
        print("[bootstrap] START uv sync --dev --frozen --python 3.13", flush=True)
        receipt["bootstrap"] = run_logged(
            ["uv", "sync", "--dev", "--frozen", "--python", "3.13"], worktree, env, logs / "bootstrap.log"
        )
        write_receipt(receipt_path, receipt)
        print(
            f"[bootstrap] {receipt['bootstrap']['status'].upper()} exit={receipt['bootstrap']['exit_code']}", flush=True
        )

        pg_ready = migration_ready = False
        if PG_TARGETS & set(targets):
            pg_run = record_command(
                receipt,
                receipt_path,
                "postgres_setup",
                "postgres-run",
                [
                    "docker",
                    "run",
                    "-d",
                    "--rm",
                    "--name",
                    pg_name,
                    "-e",
                    "POSTGRES_USER=agent_lb",
                    "-e",
                    "POSTGRES_PASSWORD=agent_lb",
                    "-e",
                    "POSTGRES_DB=agent_lb",
                    "-p",
                    "127.0.0.1::5432",
                    "postgres:16",
                ],
                worktree,
                env,
                logs / "postgres-run.log",
                120,
            )
            pg_port = record_command(
                receipt,
                receipt_path,
                "postgres_setup",
                "postgres-port",
                ["docker", "port", pg_name, "5432/tcp"],
                worktree,
                env,
                logs / "postgres-port.log",
                30,
            )
            pg_ready_entry = record_command(
                receipt,
                receipt_path,
                "postgres_setup",
                "postgres-ready",
                ["docker", "exec", pg_name, "sh", "-c", "until pg_isready -U agent_lb; do sleep 1; done"],
                worktree,
                env,
                logs / "postgres-ready.log",
                45,
            )
            if successful(pg_run) and successful(pg_port) and successful(pg_ready_entry):
                port = Path(pg_port["log_path"]).read_text(encoding="utf-8").strip().rsplit(":", 1)[-1]
                if port.isdigit():
                    pg_url = f"postgresql+asyncpg://agent_lb:agent_lb@127.0.0.1:{port}/agent_lb"
                    pg_ready = True
                else:
                    pg_port.update(status="failed", error="docker port output did not contain a numeric port")
                    write_receipt(receipt_path, receipt)
            createdb = record_command(
                receipt,
                receipt_path,
                "postgres_setup",
                "postgres-migration-createdb",
                ["docker", "exec", pg_name, "createdb", "-U", "agent_lb", "agent_lb_migration"],
                worktree,
                env,
                logs / "postgres-migration-createdb.log",
                60,
            )
            migration_ready = pg_ready and successful(createdb)

        for index, target in enumerate(targets):
            log, leg_env, blocked = logs / f"{index:02d}-{target}.log", env.copy(), None
            if target in PG_TARGETS:
                if not pg_ready or pg_url is None:
                    blocked = "disposable PostgreSQL setup failed"
                else:
                    leg_env.update(POSTGRES_TEST_DATABASE_URL=pg_url, AGENT_LB_TEST_DATABASE_URL=pg_url)
            if target == "migration-check-postgres":
                if not migration_ready or pg_url is None:
                    blocked = "disposable PostgreSQL migration database setup failed"
                else:
                    migration_url = pg_url.rsplit("/", 1)[0] + "/agent_lb_migration"
                    leg_env.update(POSTGRES_TEST_DATABASE_URL=migration_url, AGENT_LB_TEST_DATABASE_URL=migration_url)
            print(f"[leg {index + 1}/{len(targets)}] START {target}", flush=True)
            entry = (
                blocked_leg(target, log, blocked) if blocked else run_logged(["make", target], worktree, leg_env, log)
            )
            entry["target"] = target
            receipt["legs"].append(entry)
            write_receipt(receipt_path, receipt)
            outcome = (
                f"exit={entry['exit_code']} timeout={entry['timed_out']} "
                f"duration={entry['duration_seconds']}s"
            )
            print(f"[leg {index + 1}/{len(targets)}] {entry['status'].upper()} {target} {outcome}", flush=True)
        receipt["worktree_clean_after"] = clean(worktree) and worktree_at(worktree, exact_sha)
    except BaseException as exc:
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        print(f"[runner] FAILED {receipt['error']}", file=sys.stderr, flush=True)
    finally:
        if PG_TARGETS & set(targets):
            record_command(
                receipt,
                receipt_path,
                "cleanup",
                "postgres",
                ["docker", "rm", "-f", pg_name],
                root,
                env,
                logs / "cleanup-postgres.log",
                60,
            )
        if "helm-smoke-kind" in targets:
            record_command(
                receipt,
                receipt_path,
                "cleanup",
                "cluster",
                ["kind", "delete", "cluster", "--name", env["CI_CLUSTER"]],
                root,
                env,
                logs / "cleanup-cluster.log",
                180,
            )
        if IMAGE_TARGETS & set(targets):
            record_command(
                receipt,
                receipt_path,
                "cleanup",
                "image",
                ["docker", "image", "rm", "-f", env["CI_IMAGE"]],
                root,
                env,
                logs / "cleanup-image.log",
                120,
            )
        if worktree.exists():
            record_command(
                receipt,
                receipt_path,
                "cleanup",
                "worktree",
                ["git", "worktree", "remove", "--force", str(worktree)],
                root,
                env,
                logs / "cleanup-worktree.log",
                60,
            )
        else:
            receipt["cleanup"].append(blocked_cleanup(worktree, logs / "cleanup-worktree.log"))
        receipt["passed"] = compute_passed(receipt, targets)
        receipt["ended_at"] = now()
        write_receipt(receipt_path, receipt)
        signal.signal(signal.SIGTERM, previous_sigterm)
    print(receipt_path, flush=True)
    return 0 if receipt["passed"] else 1


def blocked_cleanup(worktree: Path, log: Path) -> dict[str, Any]:
    log.write_text("worktree was not created\n", encoding="utf-8")
    return {
        "label": "worktree",
        "command": ["git", "worktree", "remove", "--force", str(worktree)],
        "exit_code": 125,
        "status": "failed",
        "timed_out": False,
        "duration_seconds": 0.0,
        "log_path": str(log),
        "log_sha256": sha256(log),
        "not_run_reason": "worktree was not created",
    }


def latest_receipt(home: Path, sha: str) -> Path | None:
    if not valid_sha(sha):
        return None
    receipts = sorted((home / "receipts" / sha).glob("*/receipt.json"), key=lambda item: item.stat().st_mtime)
    return receipts[-1] if receipts else None


def valid_logged_entry(entry: Any) -> bool:
    if not successful(entry):
        return False
    try:
        log_path = Path(entry["log_path"])
        return log_path.is_file() and sha256(log_path) == entry["log_sha256"]
    except (KeyError, OSError, TypeError, ValueError):
        return False


def validate_receipt(receipt_path: Path, sha: str) -> tuple[bool, str]:
    if not valid_sha(sha):
        return False, "SHA must be exactly 40 lowercase hexadecimal characters"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if (
            receipt.get("schema_version") != SCHEMA_VERSION
            or receipt.get("exact_sha") != sha
            or receipt.get("passed") is not True
        ):
            return False, "receipt is not a passing receipt for this SHA"
        runner = receipt.get("runner", {})
        if runner.get("hostname") != socket.gethostname() or runner.get("user") != os.environ.get("USER", "unknown"):
            return False, "receipt was created by another machine or user"
        required, legs = receipt.get("required_targets"), receipt.get("legs")
        if not isinstance(required, list) or not isinstance(legs, list):
            return False, "receipt legs are malformed"
        if len(required) != len(set(required)) or set(required) != BASELINE_TARGETS:
            return False, "receipt targets are missing, extra or duplicated"
        if [leg.get("target") for leg in legs if isinstance(leg, dict)] != required:
            return False, "receipt targets are missing or reordered"
        bootstrap = receipt.get("bootstrap")
        if not receipt.get("worktree_clean_before") or not receipt.get("worktree_clean_after"):
            return False, "receipt contains dirty or changed worktree state"
        if not valid_logged_entry(bootstrap) or bootstrap.get("command") != [
            "uv",
            "sync",
            "--dev",
            "--frozen",
            "--python",
            "3.13",
        ]:
            return False, "receipt bootstrap evidence is invalid"
        if any(
            not valid_logged_entry(leg) or leg.get("command") != ["make", target]
            for target, leg in zip(required, legs, strict=True)
        ):
            return False, "receipt target evidence is invalid"
        setup, cleanup = receipt.get("postgres_setup"), receipt.get("cleanup")
        if not isinstance(setup, list) or not isinstance(cleanup, list):
            return False, "receipt setup or cleanup evidence is malformed"
        expected_setup, expected_cleanup = expected_labels(required)
        if {entry.get("label") for entry in setup if isinstance(entry, dict)} != expected_setup:
            return False, "receipt setup evidence is incomplete"
        if {entry.get("label") for entry in cleanup if isinstance(entry, dict)} != expected_cleanup:
            return False, "receipt cleanup evidence is incomplete"
        if not all(valid_logged_entry(entry) for entry in setup + cleanup):
            return False, "receipt setup or cleanup failed, timed out, or was tampered"
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False, "receipt is unreadable"
    return True, "validated passing receipt"


def status(sha: str) -> int:
    if not valid_sha(sha):
        print("SHA must be exactly 40 lowercase hexadecimal characters", file=sys.stderr)
        return 2
    home = Path(os.environ.get("AGENT_LB_CI_HOME", str(Path.home() / "agent-lb-ci"))).expanduser()
    receipt = latest_receipt(home, sha)
    if not receipt:
        print("no local receipt", file=sys.stderr)
        return 1
    ok, message = validate_receipt(receipt, sha)
    print(f"{receipt}: {message}")
    return 0 if ok else 1


def doctor() -> int:
    env = scrubbed_env(Path("/tmp/agent-lb-ci-doctor"), "doctor")
    missing = [tool for tool in REQUIRED_TOOLS if shutil.which(tool, path=env.get("PATH")) is None]
    try:
        docker_ok = (
            subprocess.run(
                ["docker", "info"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30
            ).returncode
            == 0
            if "docker" not in missing
            else False
        )
    except (OSError, subprocess.TimeoutExpired):
        docker_ok = False
    for tool in REQUIRED_TOOLS:
        print(f"{tool}: {'ok' if tool not in missing else 'missing'}")
    print(f"docker-daemon: {'ok' if docker_ok else 'unavailable'}")
    return 0 if not missing and docker_ok else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("doctor")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("ref", nargs="?", default="HEAD")
    for action in ("status", "show"):
        child = sub.add_parser(action)
        child.add_argument("sha")
    args = parser.parse_args()
    if args.action == "doctor":
        return doctor()
    if args.action == "run":
        return run(args.ref)
    if not valid_sha(args.sha):
        print("SHA must be exactly 40 lowercase hexadecimal characters", file=sys.stderr)
        return 2
    home = Path(os.environ.get("AGENT_LB_CI_HOME", str(Path.home() / "agent-lb-ci"))).expanduser()
    receipt = latest_receipt(home, args.sha)
    if args.action == "show":
        if not receipt:
            return 1
        print(receipt.read_text(encoding="utf-8"), end="")
        return 0
    return status(args.sha)


if __name__ == "__main__":
    raise SystemExit(main())
