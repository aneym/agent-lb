from __future__ import annotations

import os
import re
import stat
import subprocess
import time
from pathlib import Path

from scripts.lb_sandbox_custody import (
    ALIAS_CTIME_SLACK_NS,
    PATH_FLAGS,
    READ_FLAGS,
    VOLUME_EVENT_LOG,
    Detector,
    LiveInodes,
    live_trees,
    open_top,
    root_text,
    scan_stream,
    secret_patterns,
    under,
    volume_top,
)
from scripts.lb_sandbox_filesystem import (
    open_dir,
    remove_recorded,
    single_link,
)
from scripts.lb_sandbox_guards import (
    CUSTODY_FILES,
    DIR_FLAGS,
    DISABLED_SCHEDULERS,
    DRAIN_S,
    ENV_MARKER,
    Confinement,
    identity,
)
from scripts.lb_sandbox_system import (
    names_root,
    own_lineage,
    pid_alive,
    processes,
    run_argument,
)


def walk_files(
    paths: list[Path],
    newer_than: float | None,
    exclude: list[Path],
    unreadable: list[str],
    skip_exact: list[Path] | None = None,
    root: Path | None = None,
):

    excluded = {os.path.realpath(e) for e in exclude}
    skipped = {os.path.join(os.path.realpath(p.parent), p.name) for p in (skip_exact or [])}
    base = root_text(root) if root is not None else None
    trees = live_trees()

    live = LiveInodes()

    def is_excluded(path: str) -> bool:
        return any(under(path, e) for e in excluded)

    def checked_file(fd: int, path: str, expect: tuple[int, int] | None):
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (expect is not None and (info.st_dev, info.st_ino) != expect):
            unreadable.append(path)
            return None
        if info.st_nlink != 1:
            if (base is not None and under(path, base)) or any(under(path, t) for t in trees):
                unreadable.append(path)
                return None
            ident = (info.st_dev, info.st_ino)
            if live.started_ns is None or (
                ident not in live.ids and info.st_ctime_ns >= live.started_ns - ALIAS_CTIME_SLACK_NS
            ):
                live.refresh()
            if ident in live.ids:
                unreadable.append(path)
                return None
        if newer_than is not None and info.st_mtime <= newer_than:
            return None
        return fd

    def volume_event_log(name: str, listed: os.stat_result, dir_fd: int) -> bool:
        return (
            name == VOLUME_EVENT_LOG
            and stat.S_ISDIR(listed.st_mode)
            and listed.st_uid == 0
            and os.getuid() != 0
            and listed.st_dev == os.fstat(dir_fd).st_dev
        )

    def walk(dir_fd: int, path: str):
        try:
            with os.scandir(dir_fd) as listing:
                entries = sorted((e.name, e.is_symlink(), e.is_dir(follow_symlinks=False)) for e in listing)
        except FileNotFoundError:
            return
        except OSError:
            unreadable.append(path)
            return
        for name, is_link, is_dir in entries:
            child = os.path.join(path, name)
            if is_link or is_excluded(child):
                continue
            try:
                if is_dir:
                    listed = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                    if volume_event_log(name, listed, dir_fd) and (path == base or volume_top(dir_fd)):
                        continue
                    fd = os.open(name, DIR_FLAGS | os.O_NONBLOCK, dir_fd=dir_fd)
                    try:
                        opened = os.fstat(fd)
                        if (opened.st_dev, opened.st_ino) != (listed.st_dev, listed.st_ino):
                            unreadable.append(child)
                            continue
                        yield from walk(fd, child)
                    finally:
                        os.close(fd)
                    continue
                if child in skipped:
                    continue
                info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    continue
                if newer_than is not None and info.st_mtime <= newer_than:
                    continue
                fd = os.open(name, READ_FLAGS, dir_fd=dir_fd)
            except FileNotFoundError:
                continue
            except OSError:
                unreadable.append(child)
                continue
            if checked_file(fd, child, (info.st_dev, info.st_ino)) is None:
                os.close(fd)
                continue
            yield child, fd

    for top in paths:
        if is_excluded(os.path.realpath(top)) or is_excluded(os.path.abspath(top)):
            continue
        try:
            fd, text = open_top(Path(top), root)
        except FileNotFoundError:
            continue
        except OSError:
            unreadable.append(os.path.abspath(top))
            continue
        try:
            info = os.fstat(fd)
            if stat.S_ISDIR(info.st_mode):
                yield from walk(fd, text)
            elif stat.S_ISREG(info.st_mode) and text not in skipped and checked_file(fd, text, None) is not None:
                yield text, os.dup(fd)
        finally:
            os.close(fd)


def scan_fd(fd: int, detector: Detector, chunk: int = 4 << 20) -> tuple[bool, int]:

    with os.fdopen(fd, "rb") as fh:
        return scan_stream(fh.read, detector, chunk=chunk)


def scan_file(path: Path, detector: Detector, chunk: int = 4 << 20) -> bool:

    return scan_fd(os.open(path, PATH_FLAGS), detector, chunk)[0]


def scan_paths(
    paths: list[Path],
    secrets: list[str],
    newer_than: float | None = None,
    exclude: list[Path] | None = None,
    skip_exact: list[Path] | None = None,
    keys: list[str] | None = None,
    root: Path | None = None,
) -> dict:

    detector = Detector(secrets, keys)
    hits: list[str] = []
    unreadable: list[str] = []
    files: list[str] = []
    total = 0
    if detector:
        for path, fd in walk_files(paths, newer_than, exclude or [], unreadable, skip_exact, root):
            try:
                found, size = scan_fd(fd, detector)
            except OSError:
                unreadable.append(path)
                continue
            total += size
            files.append(path)
            if found:
                hits.append(path)
    return {
        "found": bool(hits),
        "complete": bool(detector) and not unreadable,
        "files_scanned": len(files),
        "bytes_scanned": total,
        "unreadable": len(unreadable),
        "unreadable_paths": unreadable[:20],
        "hits": hits,
        "secrets_loaded": len(secrets),
        "_files": files,
    }


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


class LeakFound(OSError):
    pass


def custody_inodes(root: Path) -> set[tuple[int, int]]:

    found = set()
    try:
        data_fd = open_dir(root, "data")
    except OSError:
        return found
    try:
        for rel in CUSTODY_FILES:
            try:
                info = os.stat(Path(rel).name, dir_fd=data_fd, follow_symlinks=False)
            except OSError:
                continue
            found.add((info.st_dev, info.st_ino))
    finally:
        os.close(data_fd)
    return found


def export_logs(root: Path, dest: Path, scanned_files: list[str], detector: Detector) -> dict:

    if not detector:
        raise OSError("no secret loaded; an unscanned export is refused")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

    def directory(path: Path) -> int:

        absolute = path.absolute()
        fd = os.open(absolute.anchor, flags)
        try:
            for part in absolute.parts[1:]:
                if part in (".", ".."):
                    raise OSError("unsafe export directory")
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
            return fd
        except BaseException:
            os.close(fd)
            raise

    custody = custody_inodes(root)
    copied, total = 0, 0
    base = Path(root_text(root)) / "logs"
    dest_fd = directory(dest)
    try:
        for name in scanned_files:
            try:
                rel = Path(name).relative_to(base)
            except ValueError:
                continue
            if not rel.parts or any(part in (".", "..") for part in rel.parts):
                raise OSError("unsafe export filename")
            src_dir = open_dir(root, Path("logs", *rel.parts[:-1]))
            try:
                src_fd = os.open(rel.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=src_dir)
            finally:
                os.close(src_dir)
            with os.fdopen(single_link(src_fd, f"log {rel}"), "rb") as src:
                info = os.fstat(src.fileno())
                if (info.st_dev, info.st_ino) in custody:
                    raise Confinement(f"log {rel} is the sandbox store or key")
                parent_fd = os.dup(dest_fd)
                try:
                    for part in rel.parts[:-1]:
                        try:
                            os.mkdir(part, mode=0o700, dir_fd=parent_fd)
                        except FileExistsError:
                            pass
                        child = os.open(part, flags, dir_fd=parent_fd)
                        os.close(parent_fd)
                        parent_fd = child

                    target_fd = os.open(
                        rel.name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd
                    )
                    made = identity(os.fstat(target_fd))
                    with os.fdopen(target_fd, "wb") as out:
                        leaked, size = scan_stream(src.read, detector, write=out.write)
                        if leaked:
                            out.flush()
                            os.ftruncate(out.fileno(), 0)
                    if leaked:
                        remove_recorded(parent_fd, rel.name, made, "file")
                        raise LeakFound(f"log {rel} holds a secret; its copy was emptied and removed")
                finally:
                    os.close(parent_fd)
            copied += 1
            total += size
    finally:
        os.close(dest_fd)
    return {"files": copied, "bytes": total}


def sandbox_env(root: Path, ports: dict[str, int], run_id: str, transport: str) -> dict[str, str]:

    env = {
        "HOME": str(root / "home"),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "PYTHONPATH": str(root / "runtime"),
        "LB_SANDBOX_ROOT": str(root),
        "AGENT_LB_DATA_DIR": str(root / "data"),
        "AGENT_LB_DATABASE_URL": f"sqlite+aiosqlite:///{root / 'data' / 'store.db'}",
        "AGENT_LB_DATABASE_SQLITE_PRE_MIGRATE_BACKUP_ENABLED": "false",
        "AGENT_LB_ENCRYPTION_KEY_FILE": str(root / "data" / "encryption.key"),
        "AGENT_LB_LOCAL_INSTANCE_ID": f"sbx-{run_id}",
        "AGENT_LB_FEDERATION_PEER_URL": f"http://127.0.0.1:{ports['gate']}",
        "AGENT_LB_FEDERATION_MIRROR_INTERVAL_SECONDS": "5",
        "AGENT_LB_FEDERATION_FORWARD_REQUEST_LOGS": "false",
        "AGENT_LB_FEDERATION_PUSH_PATH": str(root / "data" / "federation-push.json"),
        "AGENT_LB_UPSTREAM_BASE_URL": f"http://127.0.0.1:{ports['edge_openai']}",
        "AGENT_LB_ANTHROPIC_UPSTREAM_BASE_URL": f"http://127.0.0.1:{ports['edge_anthropic']}",
        "AGENT_LB_UPSTREAM_STREAM_TRANSPORT": transport,
        "AGENT_LB_DASHBOARD_AUTH_MODE": "trusted_header",
        "AGENT_LB_FIREWALL_TRUST_PROXY_HEADERS": "true",
        "AGENT_LB_FIREWALL_TRUSTED_PROXY_CIDRS": "192.0.2.1/32",
        "FORWARDED_ALLOW_IPS": "",
        "AGENT_LB_PROXY_UNAUTHENTICATED_CLIENT_CIDRS": "127.0.0.1/32",
        "AGENT_LB_CONVERSATION_ARCHIVE_DIR": str(root / "data" / "conversation-archive"),
        "UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN": str(DRAIN_S),
    }
    for name in DISABLED_SCHEDULERS:
        env[f"AGENT_LB_{name}_ENABLED"] = "false"
    return env
