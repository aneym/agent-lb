from __future__ import annotations
import json
import os
import socket
import subprocess
import urllib.error
import urllib.request
from lb_sandbox_filesystem import (
    read_root_json,
)
from lb_sandbox_paths import (
    FORBIDDEN_PORTS,
    LABEL_PREFIX,
    PICK_RANGE,
    RUN_ID_RE,
    Refused,
    SANDBOXES,
    Unhealthy,
    labels_for,
    root_for,
)
from lb_sandbox_private_storage import (
    check_root,
    sandboxes_fd,
)


def http(port: int, path: str, timeout: float = 3.0) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except Exception:
        return 0, b""


def port_free(port: int) -> bool:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def sandbox_roots() -> list[Path]:

    try:
        with os.scandir(sandboxes_fd()) as listing:
            entries = sorted(((e.name, e.is_dir(follow_symlinks=False)) for e in listing), key=lambda e: e[0])
    except OSError:
        return []
    return [SANDBOXES / name for name, is_dir in entries if RUN_ID_RE.match(name) and is_dir]


def ports_in_use_by_sandboxes() -> set[int]:
    used: set[int] = set()
    for root in sandbox_roots():
        ports = read_root_json(root, "sandbox.json").get("ports", {})
        used.update(int(p) for p in (ports.values() if isinstance(ports, dict) else []) if isinstance(p, int))
    return used


def pick_ports(count: int, taken: set[int]) -> list[int]:
    picked: list[int] = []
    for port in PICK_RANGE:
        if port in FORBIDDEN_PORTS or port in taken or port in picked:
            continue
        if port_free(port):
            picked.append(port)
            if len(picked) == count:
                return picked
    raise Unhealthy("no free sandbox ports in 2470-2599")


SANDBOX_LABEL_PREFIX = f"{LABEL_PREFIX}sbx-"


def refuse_foreign_launchctl_print(argv: list[str]) -> None:

    if len(argv) >= 2 and os.path.basename(str(argv[0])) == "launchctl" and argv[1] == "print":
        target = str(argv[2]) if len(argv) > 2 else ""
        label = target.rsplit("/", 1)[-1]
        if not (target.startswith(f"gui/{os.getuid()}/") and label.startswith(SANDBOX_LABEL_PREFIX)):
            raise Refused(f"launchctl print of {target!r} refused: only a sandbox's own job is printed")


def run(argv: list[str], timeout: float = 30) -> subprocess.CompletedProcess:

    refuse_foreign_launchctl_print(argv)
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


def launchctl_jobs() -> dict[str, int | None]:

    out = run(["/bin/launchctl", "list"]).stdout
    lines = out.splitlines()
    if not lines or lines[0].split() != ["PID", "Status", "Label"]:
        raise Unhealthy("launchctl list printed no job table")
    jobs: dict[str, int | None] = {}
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) == 3:
            jobs[parts[2]] = int(parts[0]) if parts[0].isdigit() else None
    return jobs


def launchd_pid(label: str) -> int | None:
    return launchctl_jobs().get(label)


def label_loaded(label: str) -> bool:
    return label in launchctl_jobs()


def own_job_print(label: str, run_id: str) -> str:

    if label not in labels_for(run_id):
        raise Refused(f"launchctl print of {label} refused: not a job of run {run_id}")
    return run(["/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"]).stdout


def listener_pids(port: int) -> list[int]:
    out = run(["/usr/sbin/lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"]).stdout.split()
    return sorted({int(pid) for pid in out if pid.isdigit()})


def listener_addresses(pids: list[int]) -> list[dict]:

    if not pids:
        return []
    out = run(["/usr/sbin/lsof", "-nP", "-a", "-p", ",".join(map(str, pids)), "-iTCP", "-sTCP:LISTEN"]).stdout
    found = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 9:
            found.append({"pid": int(parts[1]), "address": parts[8]})
    return found


def processes() -> list[tuple[int, int, str]]:
    out = run(["/bin/ps", "-axww", "-o", "pid=,ppid=,command="]).stdout
    rows = []
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
    return rows


def names_root(cmd: str, root: Path) -> bool:

    text = str(root)
    return any(token == text or token.startswith(text + "/") for token in cmd.split())


def names_label(cmd: str, run_id: str) -> bool:

    labels = set(labels_for(run_id))
    return any(token.rsplit("/", 1)[-1] in labels for token in cmd.split() if token.startswith(("gui/", LABEL_PREFIX)))


def names_run(cmd: str, run_id: str) -> bool:

    return names_label(cmd, run_id) or run_argument(cmd, run_id)


def run_argument(cmd: str, run_id: str) -> bool:

    tokens = cmd.split()
    return any(
        token == f"--run-id={run_id}" or (token == "--run-id" and tokens[i + 1 : i + 2] == [run_id])
        for i, token in enumerate(tokens)
    )


def owned_process(cmd: str, root: Path, run_id: str) -> bool:

    return names_root(cmd, root) or names_label(cmd, run_id)


def own_lineage(rows: list[tuple[int, int, str]]) -> set[int]:
    parents = {pid: ppid for pid, ppid, _ in rows}
    lineage, pid = set(), os.getpid()
    while pid and pid not in lineage:
        lineage.add(pid)
        pid = parents.get(pid, 0)
    return lineage


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def emit(data: dict, code: int) -> int:
    print(json.dumps(data, indent=1, sort_keys=True), flush=True)
    return code


def stamped_meta(root: Path, run_id: str) -> dict:

    meta = read_root_json(root, "sandbox.json")
    if meta.get("run_id") != run_id:
        return {}
    if "label" in meta and meta["label"] != labels_for(run_id)[0]:
        return {}
    return meta


def load_meta(run_id: str) -> tuple[Path, dict]:
    root = check_root(root_for(run_id))
    meta = stamped_meta(root, run_id)
    if not meta:
        raise Unhealthy(f"no sandbox {run_id} (no sandbox.json stamped with this run id under {root})")
    return root, meta
