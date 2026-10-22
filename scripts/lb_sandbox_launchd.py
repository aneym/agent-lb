from __future__ import annotations
import os
import plistlib
import re
import shutil
import time
from pathlib import Path
from lb_sandbox_filesystem import (
    read_json,
    read_root_text,
)
from lb_sandbox_paths import (
    LIVE_FRONT_LABEL,
    LIVE_FRONT_PORT,
    LIVE_FRONT_STATE,
    LIVE_LABEL,
    LIVE_PRIMARY_PORT,
    Refused,
    Unhealthy,
    check_label,
    labels_for,
)
from lb_sandbox_processes import (
    label_loaded,
    launchd_pid,
    listener_pids,
    names_root,
    own_job_print,
    run,
)


def find_node() -> str:
    for candidate in (
        run(["volta", "which", "node"]).stdout.strip() if shutil.which("volta") else "",
        shutil.which("node") or "",
        "/opt/homebrew/bin/node",
    ):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    raise Unhealthy("node not found for the sandbox front")


def live_identity() -> dict:
    preferred = read_json(LIVE_FRONT_STATE).get("preferred")
    return {
        "primary_pid": launchd_pid(LIVE_LABEL),
        "front_pid": launchd_pid(LIVE_FRONT_LABEL),
        "front_port": LIVE_FRONT_PORT,
        "front_listener_pids": listener_pids(LIVE_FRONT_PORT),
        "primary_listener_pids": listener_pids(LIVE_PRIMARY_PORT),
        "preferred_port": preferred,
    }


def sm_job_submit(body: dict) -> bool:

    import ctypes

    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    sm = ctypes.CDLL("/System/Library/Frameworks/ServiceManagement.framework/ServiceManagement")
    cf.CFDataCreate.restype = ctypes.c_void_p
    cf.CFDataCreate.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long]
    cf.CFPropertyListCreateWithData.restype = ctypes.c_void_p
    cf.CFPropertyListCreateWithData.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    sm.SMJobSubmit.restype = ctypes.c_bool
    sm.SMJobSubmit.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    domain = ctypes.c_void_p.in_dll(sm, "kSMDomainUserLaunchd")
    data = plistlib.dumps(body, fmt=plistlib.FMT_BINARY)
    cfdata = cf.CFDataCreate(None, data, len(data))
    if not cfdata:
        return False
    job = cf.CFPropertyListCreateWithData(None, cfdata, 0, None, None)
    try:
        return bool(job) and bool(sm.SMJobSubmit(domain, job, None, None))
    finally:
        if job:
            cf.CFRelease(job)
        cf.CFRelease(cfdata)


def own_job_loaded(label: str) -> bool:

    return run(["/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"]).returncode == 0


def submit_job(body: dict, deadline_s: float = 20) -> None:

    label = check_label(str(body.get("Label", "")))
    if label_loaded(label) or own_job_loaded(label):
        raise Refused(f"{label} is already loaded; start never replaces a job")
    if not sm_job_submit(body):
        raise Unhealthy(f"launchd refused the job {label}")
    deadline = time.monotonic() + deadline_s
    while not own_job_loaded(label):
        if time.monotonic() > deadline:
            run(["/bin/launchctl", "remove", label])
            raise Unhealthy(f"the job {label} is not in gui/{os.getuid()} (a session without a GUI domain?)")
        time.sleep(0.25)


def label_names_root(label: str, root: Path, run_id: str) -> bool:

    out = own_job_print(label, run_id)
    return any(names_root(line.strip(), root) for line in out.splitlines())


def bootout(label: str, deadline_s: float = 30) -> bool:
    check_label(label)
    target = f"gui/{os.getuid()}/{label}"
    run(["/bin/launchctl", "bootout", target], timeout=deadline_s + 10)
    deadline = time.monotonic() + deadline_s
    while label_loaded(label):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.5)
    return True


def usable_counts(accounts: list[dict]) -> dict[str, int]:
    counts = {"anthropic": 0, "openai": 0}
    for account in accounts:
        if account["usable"] and account["vendor"] in counts:
            counts[account["vendor"]] += 1
    return counts


def log_failure_classes(root: Path) -> dict:

    text = read_root_text(root, "logs/primary.log", 200_000)
    if not text:
        return {}
    return {
        "exception_classes": sorted(set(re.findall(r"^([A-Za-z0-9_.]+(?:Error|Exception)):", text, re.MULTILINE))),
        "frames": re.findall(r'File "[^\n"]*/(app/[^\n"]+)", line ([0-9]+), in ([A-Za-z0-9_]+)', text)[-8:],
    }


def plist_paths(root: Path, run_id: str) -> tuple[Path, Path]:
    label, aux_label = labels_for(run_id)
    return root / "launchd" / f"{label}.plist", root / "launchd" / f"{aux_label}.plist"
