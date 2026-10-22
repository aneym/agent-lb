from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

HOME = Path.home()


LIVE_LABEL = "com.aneyman.agent-lb"


LIVE_FRONT_LABEL = "com.aneyman.agent-lb-front"


LIVE_FRONT_STATE = HOME / ".agent-lb" / "state" / "front.json"


LIVE_SYNC_LOG = HOME / ".agent-lb" / "runtime" / "sync.log"


LIVE_RESTART_LOCK = HOME / ".agent-lb" / "runtime" / "lb-restart.lock"


SYNC_RESTART_RE = re.compile(r"^\S+ lb-restart (ok|FAILED)\b")


def utc() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Check:
    run_id: str | None
    started: bool
    stopped: bool
    infra: str | None

    def __init__(self, out: Path, lb_sandbox: Path) -> None:
        self.out = out
        self.lb_sandbox = lb_sandbox
        self.n = 0
        self.lock = threading.Lock()
        self.steps: list[dict] = []
        self.env_infra: str | None = None
        self.unverified = [
            "Other hosts: Studio only; boxes use their own follower agent-lb.",
            "OAuth refresh in the sandbox: mirrored accounts carry access tokens only; refresh is refused in code.",
            "Websocket upstream: the sandbox forces http so the edge can see and cut streams.",
            "The live restart: only the sandbox restarts; the live service is read, never restarted.",
        ]

    def step(self, name: str, ok: bool, detail: str, evidence: list[str]) -> bool:
        self.steps.append({"name": name, "result": "pass" if ok else "fail", "detail": detail, "evidence": evidence})
        print(f"[{utc()}] {'PASS' if ok else 'FAIL'} {name}: {detail}", flush=True)
        return ok

    def sb(self, name: str, *args: str, timeout: float = 600) -> tuple[int, dict, Path]:

        with self.lock:
            self.n += 1
            stem = self.out / f"{self.n:02d}-{name}"
        argv = [str(self.lb_sandbox), *args, "--json"]
        with open(f"{stem}.out", "w") as so, open(f"{stem}.err", "w") as se:
            try:
                rc = subprocess.run(argv, stdout=so, stderr=se, timeout=timeout, check=False).returncode
            except subprocess.TimeoutExpired:
                rc = 124
        try:
            data = json.loads(Path(f"{stem}.out").read_text() or "{}")
        except ValueError:
            data = {}
        return rc, data, Path(f"{stem}.out")


def sh(*argv: str) -> str:
    return subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False).stdout


def launchd_pid(label: str) -> int | None:

    for line in sh("/bin/launchctl", "list").splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) == 3 and parts[2] == label:
            return int(parts[0]) if parts[0].isdigit() else None
    return None


def listener_pids(port: int) -> list[int]:
    return sorted({int(p) for p in sh("/usr/sbin/lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t").split()})


def live_identity() -> dict:
    try:
        preferred = json.loads(LIVE_FRONT_STATE.read_text()).get("preferred")
    except (OSError, ValueError):
        preferred = None
    return {
        "primary_pid": launchd_pid(LIVE_LABEL),
        "front_pid": launchd_pid(LIVE_FRONT_LABEL),
        "listeners_2455": listener_pids(2455),
        "listeners_2457": listener_pids(2457),
        "preferred_port": preferred,
    }


def sync_log_size() -> int:

    try:
        return LIVE_SYNC_LOG.stat().st_size
    except OSError:
        return 0


def live_restarts_since(offset: int) -> list[str]:

    try:
        with open(LIVE_SYNC_LOG, "rb") as fh:
            fh.seek(offset)
            text = fh.read(1 << 20).decode("utf-8", "replace")
    except OSError:
        return []
    return [line[:300] for line in text.splitlines() if SYNC_RESTART_RE.match(line)]


def live_restart_holder() -> str | None:

    try:
        text = LIVE_RESTART_LOCK.read_text(errors="replace").strip()
    except OSError:
        return None
    return text[:300] or None


def live_untouched(before: dict, after: dict, restarts: list[str], holders: list[str]) -> tuple[str, str]:

    if after == before:
        return "pass", f"before {before}; after {after}"
    named = [f"logged: {line}" for line in restarts] + [f"in progress: {line}" for line in holders]
    if named:
        return "infra_error", (
            f"live agent-lb restarted by another lane during the run ({'; '.join(named)}); "
            f"before {before}; after {after}"
        )
    return "fail", f"live identity changed with no live lb-restart logged or running: before {before}; after {after}"


def resolve_model(alias: str, fallback: str) -> str:
    route = shutil.which("route")
    if route:
        value = sh(route, "resolve", alias).strip().splitlines()
        if value and value[0].strip():
            return value[0].strip()
    return fallback


def leftover_pids(run_id: str) -> list[int]:

    leftover = []
    for row in sh("/bin/ps", "-axww", "-o", "pid=,command=").splitlines():
        parts = row.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit() and int(parts[0]) != os.getpid() and run_id in parts[1]:
            leftover.append(int(parts[0]))
    return leftover
