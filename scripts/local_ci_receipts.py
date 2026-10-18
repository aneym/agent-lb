from __future__ import annotations

import hashlib
import json
import os
import re
import socket
from pathlib import Path
from typing import Any, cast

SCHEMA_VERSION = 1

PG_TARGETS = {"test-postgres", "migration-check-postgres"}

IMAGE_TARGETS = {"docker"}

BASELINE_TARGETS = {
    "frontend-build",
    "lint",
    "test-floor",
    "test-integration-core",
    "test-integration-bridge",
    "test-e2e",
    "test-postgres",
    "migration-check",
    "migration-check-postgres",
    "package",
    "docker",
    "menubar-build",
}

SHA_RE = re.compile(r"[0-9a-f]{40}\Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def valid_sha(value: str) -> bool:
    return bool(SHA_RE.fullmatch(value))


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


def expected_labels(targets: list[str]) -> tuple[set[str], set[str]]:
    setup = {"postgres-run", "postgres-port", "postgres-ready"} if PG_TARGETS & set(targets) else set()
    if "migration-check-postgres" in targets:
        setup.add("postgres-migration-createdb")
    cleanup = {"worktree"}
    if PG_TARGETS & set(targets):
        cleanup.add("postgres")
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
            not isinstance(leg, dict)
            or not valid_logged_entry(leg)
            or cast(dict[str, Any], leg).get("command") != ["make", target]
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
