"""lb-sandbox-check verdicts: when a run may count as proof that an agent-lb restart is safe.

Unit level on purpose: each verdict is a decision table over lb-sandbox's JSON and the lb-restart log, with
many ways to be wrong, and the live run that feeds it (launchd, the live pool) cannot run in CI. The check
authorizes agent-lb restarts, so every row here is a way a run must fail rather than pass.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "lb-sandbox-check"


def _load() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("lb_sandbox_check_under_test", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


CUTOVER_LOG = (
    "[lb-restart +   1.8s] standby healthy on :2472 in 1.5s (pid 1280)\n"
    "[lb-restart +   3.5s] front -> :2472; primary pid 92640 had {n} in flight, 1 connection(s); draining it\n"
)
GOOD_RESTART = {
    "cutover": {"standby_port": 2472, "old_pid": 92640, "in_flight": 1},
    "hold": {"fault": "hold_stream", "released_by": "clear", "held_s": 4.2},
    "released_after_cutover": True,
    "rc": 0,
    "restart": {"lb_restart_exit": 0},
    "marks": {"standby_boot": True, "front_switch": True, "drain": True, "restore": True, "standby_stopped": True},
    "switched": True,
    "stream": {"status": 200, "complete": True},
    "stream_done": True,
}


def test_cutover_line_is_parsed_from_the_lb_restart_log() -> None:
    check = _load()
    assert check.parse_cutover(CUTOVER_LOG.format(n=1)) == {"standby_port": 2472, "old_pid": 92640, "in_flight": 1}
    assert check.parse_cutover(CUTOVER_LOG.format(n=0))["in_flight"] == 0
    assert check.parse_cutover(CUTOVER_LOG.format(n="None"))["in_flight"] is None
    assert check.parse_cutover("standby healthy on :2472\n") is None


@pytest.mark.parametrize(
    "override",
    [
        # The c957bb05e false pass: the stream finished before the cutover, so 0 were in flight.
        {"cutover": {"standby_port": 2472, "old_pid": 92640, "in_flight": 0}},
        {"cutover": {"standby_port": 2472, "old_pid": 92640, "in_flight": None}},
        {"cutover": None},
        {"hold": None},
        {"hold": {"fault": "hold_stream", "released_by": "timeout", "held_s": 30.0}},
        {"released_after_cutover": False},
        {"rc": 3},
        {"rc": None},
        {"restart": {"lb_restart_exit": 1}},
        {"restart": {}},
        {"marks": {}},
        {"marks": {**GOOD_RESTART["marks"], "drain": False}},
        {"switched": False},
        {"stream": {"status": 200, "complete": False}},
        {"stream": {}},
        {"stream_done": False},
    ],
)
def test_restart_proof_fails_on_any_doubt(override: dict) -> None:
    check = _load()
    assert check.restart_problems(**GOOD_RESTART) == []
    assert check.restart_problems(**{**GOOD_RESTART, **override}) != []


GOOD_SCAN = {"found": False, "complete": True, "secrets_loaded": 9, "files_scanned": 40, "hits": []}


@pytest.mark.parametrize(
    ("rc", "scan"),
    [
        (1, {**GOOD_SCAN, "found": True, "hits": ["/x"]}),
        (0, {**GOOD_SCAN, "found": True}),
        (0, {**GOOD_SCAN, "complete": False}),
        (0, {**GOOD_SCAN, "secrets_loaded": 1}),
        (0, {k: v for k, v in GOOD_SCAN.items() if k != "found"}),
        (2, GOOD_SCAN),
        (None, {}),
    ],
)
def test_a_scan_counts_only_when_complete_and_clean(rc, scan: dict) -> None:
    check = _load()
    assert check.scan_problems("x", 0, GOOD_SCAN) == []
    assert check.scan_problems("x", rc, scan) != []


def test_a_keyed_scan_must_have_held_the_store_key() -> None:
    """Without the key a store snapshot taken before a mirror cycle reads clean (finding on 41a2f667)."""
    check = _load()
    assert check.scan_problems("x", 0, {**GOOD_SCAN, "ciphertext_keys": 1}, keyed=True) == []
    assert check.scan_problems("x", 0, {**GOOD_SCAN, "ciphertext_keys": 0}, keyed=True) != []
    assert check.scan_problems("x", 0, GOOD_SCAN, keyed=True) != []


def test_process_scan_must_read_every_named_process_environment() -> None:
    check = _load()
    procs = {"found": False, "complete": True, "visible_pids": [10, 11, 12], "pids": [10, 11, 12]}
    good = {**GOOD_SCAN, "processes": procs}
    assert check.process_scan_problems(0, good, {"primary": 10, "aux": 11, "front": 12}) == []
    assert check.process_scan_problems(0, good, {"primary": 10, "standby": 13}) != []  # env never shown
    # Finding on 41a2f667: an extra run process (no named role) whose env ps did not show still passed.
    extra = {**procs, "pids": [10, 11, 12, 13]}
    assert check.process_scan_problems(0, {**good, "processes": extra}, {"primary": 10}) != []
    hidden = {**procs, "invisible_pids": [14]}
    assert check.process_scan_problems(0, {**good, "processes": hidden}, {"primary": 10}) != []
    assert check.process_scan_problems(0, good, {"primary": None}) != []
    assert check.process_scan_problems(0, {**good, "processes": {**procs, "found": True}}, {"primary": 10}) != []
    assert check.process_scan_problems(0, {**GOOD_SCAN}, {"primary": 10}) != []


GOOD_STOP = {
    "clean": True,
    "logs": {
        "scan_scope": "root",
        "scan_found": False,
        "scan_complete": True,
        "copied": True,
        "files_copied": 8,
        "export_scanned": True,
        "ciphertext_keys": 1,
    },
    "custody": {"key_unlinked": True},
}


@pytest.mark.parametrize(
    ("rc", "stop", "leftover", "labels"),
    [
        # The c957bb05e false pass: teardown found a token, refused the export, deleted the root, said clean.
        (0, {**GOOD_STOP, "logs": {**GOOD_STOP["logs"], "scan_found": True, "copied": False}}, [], ""),
        (0, {**GOOD_STOP, "logs": {**GOOD_STOP["logs"], "scan_complete": False}}, [], ""),
        (0, {**GOOD_STOP, "logs": {k: v for k, v in GOOD_STOP["logs"].items() if k != "scan_scope"}}, [], ""),
        (0, {**GOOD_STOP, "logs": {**GOOD_STOP["logs"], "secret_load_error": "Refused"}}, [], ""),
        (0, {**GOOD_STOP, "logs": {**GOOD_STOP["logs"], "copy_error": "OSError", "copied": False}}, [], ""),
        (0, {**GOOD_STOP, "custody": {"key_unlinked": False}}, [], ""),
        (0, {**GOOD_STOP, "custody": {}}, [], ""),
        (1, GOOD_STOP, [], ""),
        (0, {**GOOD_STOP, "clean": False}, [], ""),
        (0, GOOD_STOP, [123], ""),
        # Finding on 41a2f667: the export was never rescanned; a link swap after the scan copied the key out.
        (0, {**GOOD_STOP, "logs": {**GOOD_STOP["logs"], "export_scanned": False}}, [], ""),
        (0, {**GOOD_STOP, "logs": {**GOOD_STOP["logs"], "export_found": True}}, [], ""),
        (0, {**GOOD_STOP, "logs": {**GOOD_STOP["logs"], "files_copied": 0}}, [], ""),
        (0, {**GOOD_STOP, "logs": {**GOOD_STOP["logs"], "ciphertext_keys": 0}}, [], ""),
        (0, GOOD_STOP, [], "456\t0\tcom.agent-lb.drill.sbx-r1"),
    ],
)
def test_stop_counts_only_when_the_teardown_scan_is_clean(rc, stop: dict, leftover: list, labels: str) -> None:
    check = _load()
    assert check.stop_problems(0, GOOD_STOP, [], "", "r1") == []
    assert check.stop_problems(rc, stop, leftover, labels, "r1") != []


def test_custody_parity_wants_private_directories_and_key(tmp_path: Path) -> None:
    check = _load()
    sandboxes = tmp_path / "sandboxes"
    root = sandboxes / "r1"
    (root / "data").mkdir(parents=True)
    key = root / "data" / "encryption.key"
    key.write_bytes(b"k" * 44)
    for path, mode in ((sandboxes, 0o700), (root, 0o700), (key, 0o600)):
        path.chmod(mode)
    assert check.custody_problems(sandboxes, root) == []
    root.chmod(0o755)
    assert check.custody_problems(sandboxes, root) != []
    root.chmod(0o700)
    key.chmod(0o644)
    assert check.custody_problems(sandboxes, root) != []
    key.chmod(0o600)
    (tmp_path / "copy.key").hardlink_to(key)
    assert check.custody_problems(sandboxes, root) != []


def test_a_run_refuses_an_out_dir_another_run_holds(tmp_path: Path) -> None:
    """CLI boundary, run as a subprocess: two verifiers once shared one --out, overwrote each other's step files and
    both failed on the other's output. A second run must refuse the dir, exit 2 and leave the first run's files
    as they were. --lb-sandbox points nowhere, so even a run that wrongly proceeds starts no sandbox."""
    out = tmp_path / "run"
    out.mkdir()
    first = out / "result.json"
    first.write_text('{"result": "pass"}\n')
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(out), "--lb-sandbox", str(tmp_path / "no-lb-sandbox")],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 2
    verdict = json.loads(proc.stdout)
    assert verdict["result"] == "infra_error"
    assert "--out" in verdict["summary"]
    assert sorted(p.name for p in out.iterdir()) == ["result.json"]
    assert first.read_text() == '{"result": "pass"}\n'
