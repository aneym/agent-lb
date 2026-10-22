from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from .detection import (
    secret_patterns,
)
from .paths import (
    ENV_MARKER,
)
from .processes import (
    names_root,
    own_lineage,
    pid_alive,
    processes,
    run_argument,
)


def ps_command(pid: int, with_env: bool) -> bytes:
    flags = ["-E"] if with_env else []
    return subprocess.run(
        ["/bin/ps", *flags, "-ww", "-o", "command=", "-p", str(pid)], capture_output=True, timeout=30, check=False
    ).stdout.rstrip(b"\n")


def env_shows_root(env_text: bytes, root: Path) -> bool:

    marker = re.escape(ENV_MARKER.encode() + str(root).encode())
    return re.search(rb"(?:^|\s)" + marker + rb"(?:\s|$)", env_text) is not None


PROCESS_SETTLE_S = 2.0


def run_processes(rows: list[tuple[int, int, str]], root: Path, run_id: str) -> tuple[list[int], list[int]]:

    lineage = own_lineage(rows)
    named = {pid for pid, _, cmd in rows if (names_root(cmd, root) or run_argument(cmd, run_id)) and pid not in lineage}
    children: dict[int, list[int]] = {}
    for pid, ppid, _ in rows:
        if pid != ppid:
            children.setdefault(ppid, []).append(pid)
    below: set[int] = set()
    stack = sorted(named)
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in named and child not in below and child not in lineage:
                below.add(child)
                stack.append(child)
    return sorted(named), sorted(below)


def executable_path(pid: int) -> str | None:

    try:
        import ctypes

        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        buf = ctypes.create_string_buffer(4096)
        size = libproc.proc_pidpath(ctypes.c_int(pid), buf, ctypes.c_uint32(len(buf)))
    except (OSError, AttributeError):
        return None
    return buf.value.decode(errors="replace") if size > 0 else None


INHERITING_TOOLS = frozenset({"/bin/launchctl", "/bin/ps", "/usr/sbin/lsof"})


def zombie(pid: int) -> bool:

    out = subprocess.run(["/bin/ps", "-o", "stat=", "-p", str(pid)], capture_output=True, timeout=30, check=False)
    return out.stdout.strip().startswith(b"Z")


def env_rest(argv_text: bytes, text: bytes) -> bytes | None:

    if not argv_text.strip() or not text.startswith(argv_text):
        return None
    return text[len(argv_text) :]


def scan_processes(root: Path, run_id: str, secrets: list[str]) -> dict:

    patterns = sorted({p for s in secrets if s for p in secret_patterns(s)})
    rows = processes()
    named, below = run_processes(rows, root, run_id)
    parent_of = {pid: ppid for pid, ppid, _ in rows}
    pids = sorted({*named, *below})

    def shown_for(pid: int, argv_text: bytes, text: bytes) -> bool:
        rest = env_rest(argv_text, text)
        if rest is None:
            return False
        if pid in below:
            return bool(rest.strip())
        return env_shows_root(rest, root)

    hits, visible, invisible, unreadable, scanned, inherited = [], [], [], [], [], []
    for pid in pids:
        argv_text = ps_command(pid, with_env=False)
        text = ps_command(pid, with_env=True)
        if not text.strip() or not argv_text.strip():
            if pid_alive(pid) and not zombie(pid):
                unreadable.append(pid)
            continue
        scanned.append(pid)
        if any(p in text for p in patterns):
            hits.append(pid)
        shown = shown_for(pid, argv_text, text)

        deadline = time.monotonic() + PROCESS_SETTLE_S
        while not shown and pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
            argv_text, text = ps_command(pid, with_env=False), ps_command(pid, with_env=True)
            if any(p in text for p in patterns) and pid not in hits:
                hits.append(pid)
            shown = shown_for(pid, argv_text, text)
        if shown:
            visible.append(pid)
        elif pid_alive(pid) and not zombie(pid):
            invisible.append(pid)
        elif pid not in hits:
            scanned.remove(pid)
    for pid in list(invisible):
        if pid in below and parent_of.get(pid) in visible and executable_path(pid) in INHERITING_TOOLS:
            invisible.remove(pid)
            scanned.remove(pid)
            inherited.append(pid)
    return {
        "found": bool(hits),
        "complete": bool(patterns) and not unreadable and not invisible and bool(scanned),
        "processes_scanned": len(scanned),
        "pids": scanned,
        "descendant_pids": [pid for pid in below if pid in scanned],
        "visible_pids": visible,
        "invisible_pids": invisible,
        "unreadable_pids": unreadable,
        "tool_pids": inherited,
        "hits": hits,
        "secrets_loaded": len(secrets),
    }
