from __future__ import annotations

import re
import stat
from pathlib import Path

CUTOVER_RE = re.compile(r"front -> :(\d+); primary pid (\d+) had (-?\d+|None) in flight")


def parse_cutover(log_text: str) -> dict | None:

    match = CUTOVER_RE.search(log_text)
    if not match:
        return None
    flight = match.group(3)
    return {
        "standby_port": int(match.group(1)),
        "old_pid": int(match.group(2)),
        "in_flight": int(flight) if flight.lstrip("-").isdigit() else None,
    }


def restart_problems(
    cutover: dict | None,
    hold: dict | None,
    released_after_cutover: bool,
    rc: int | None,
    restart: dict,
    marks: dict,
    switched: bool,
    stream: dict,
    stream_done: bool,
) -> list[str]:
    problems = []
    if not cutover:
        problems.append("no cutover line in the lb-restart log")
    elif not isinstance(cutover.get("in_flight"), int) or cutover["in_flight"] < 1:
        problems.append(f"{cutover.get('in_flight')} in flight on the primary at cutover (need >= 1)")
    if not hold:
        problems.append("the edge served no held stream")
    elif hold.get("released_by") != "clear":
        problems.append(f"the held stream ended by {hold.get('released_by')}, not by the check's release")
    if not released_after_cutover:
        problems.append("the check did not release the hold after the cutover")
    if rc != 0 or restart.get("lb_restart_exit") != 0:
        problems.append(f"lb-sandbox restart exit {rc}, lb-restart exit {restart.get('lb_restart_exit')}")
    missing = sorted(name for name, seen in marks.items() if not seen)
    if not marks or missing:
        problems.append(f"lb-restart log lacks {missing or 'every mark'}")
    if not switched:
        problems.append("the front did not switch to the standby and back")
    if not stream_done:
        problems.append("the held stream was still open after the restart")
    if stream.get("status") != 200 or stream.get("complete") is not True:
        problems.append(f"stream status {stream.get('status')} complete {stream.get('complete')}")
    return problems


def scan_problems(name: str, rc: int | None, scan: dict, min_secrets: int = 2, keyed: bool = False) -> list[str]:

    problems = []
    if keyed and scan.get("ciphertext_keys") != 1:
        problems.append(f"{name}: scanned without the store key ({scan.get('ciphertext_keys')})")
    if rc != 0:
        problems.append(f"{name}: exit {rc}")
    if scan.get("found") is not False:
        problems.append(f"{name}: found {scan.get('found')} hits {scan.get('hits')}")
    if scan.get("complete") is not True:
        problems.append(f"{name}: incomplete ({scan.get('unreadable')} unreadable)")
    if not isinstance(scan.get("secrets_loaded"), int) or scan["secrets_loaded"] < min_secrets:
        problems.append(f"{name}: {scan.get('secrets_loaded')} secrets loaded (need >= {min_secrets})")
    return problems


def process_scan_problems(rc: int | None, scan: dict, must_see: dict[str, int | None]) -> list[str]:

    procs = scan.get("processes") or {}
    problems = scan_problems("process scan", rc, scan)
    if procs.get("found") is not False or procs.get("complete") is not True:
        problems.append(f"process scan: found {procs.get('found')} complete {procs.get('complete')}")
    visible = set(procs.get("visible_pids") or [])
    unseen = sorted(set(procs.get("pids") or []) - visible) + list(procs.get("invisible_pids") or [])
    if unseen:
        problems.append(f"process scan did not read the environment of pids {sorted(set(unseen))}")
    for role, pid in must_see.items():
        if not isinstance(pid, int) or pid not in visible:
            problems.append(f"process scan did not read the environment of the {role} (pid {pid})")
    return problems


def stop_problems(rc: int | None, stop: dict, leftover: list[str], labels: str, run_id: str) -> list[str]:
    logs = stop.get("logs") or {}
    custody = stop.get("custody") or {}
    problems = []
    if rc != 0 or stop.get("clean") is not True:
        problems.append(f"stop exit {rc} clean {stop.get('clean')}")
    if leftover:
        problems.append(f"processes naming the run remain: {leftover}")
    if f"sbx-{run_id}" in labels:
        problems.append("a run label is still loaded")
    if logs.get("scan_scope") != "root":
        problems.append(f"teardown scanned {logs.get('scan_scope')}, not the whole sandbox root")
    if logs.get("scan_found") is not False or logs.get("scan_complete") is not True:
        problems.append(f"teardown scan found {logs.get('scan_found')} complete {logs.get('scan_complete')}")
    if logs.get("secret_load_error") or logs.get("copy_error") or logs.get("copied") is not True:
        problems.append(f"log export: {logs}")
    if logs.get("export_found") or logs.get("export_scanned") is not True or not logs.get("files_copied"):
        problems.append(
            f"log export not scanned byte for byte: export_scanned {logs.get('export_scanned')} "
            f"found {logs.get('export_found')} files {logs.get('files_copied')}"
        )
    if logs.get("ciphertext_keys") != 1:
        problems.append(f"teardown scan ran without the store key ({logs.get('ciphertext_keys')})")
    if custody.get("key_unlinked") is not True:
        problems.append(f"store key unlinked: {custody.get('key_unlinked')}")
    return problems


def custody_problems(sandboxes: Path, root: Path) -> list[str]:

    problems = []
    for path, want in ((sandboxes, 0o700), (root, 0o700)):
        try:
            info = path.lstat()
        except OSError as exc:
            problems.append(f"{path}: {exc.__class__.__name__}")
            continue
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != want:
            problems.append(f"{path} mode {oct(stat.S_IMODE(info.st_mode))} (want {oct(want)}, a real directory)")
    key = root / "data" / "encryption.key"
    try:
        info = key.lstat()
    except OSError as exc:
        return [*problems, f"key: {exc.__class__.__name__}"]
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
        problems.append(f"key mode {oct(stat.S_IMODE(info.st_mode))} links {info.st_nlink} (want 0o600, 1)")
    return problems
