from __future__ import annotations
import errno
import os
import stat
import time
from pathlib import Path
from lb_sandbox_detection import (
    Detector,
    scan_stream,
)
from lb_sandbox_filesystem import (
    open_dir,
    open_root,
)
from lb_sandbox_paths import (
    Confinement,
    DIR_FLAGS,
    LB_HOME,
    LIVE_KEY_DIR_LEGACY,
    LIVE_PLIST,
    SANDBOXES,
)
from lb_sandbox_private_storage import (
    fd_path,
    sandboxes_fd,
)


NOFOLLOW_ANY = getattr(os, "O_NOFOLLOW_ANY", 0)


READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


ALIAS_CTIME_SLACK_NS = 2_000_000_000


PATH_FLAGS = os.O_RDONLY | os.O_NONBLOCK | (NOFOLLOW_ANY or os.O_NOFOLLOW)


VOLUME_EVENT_LOG = ".fseventsd"


def volume_top(dir_fd: int) -> bool:

    try:
        return os.fstat(dir_fd).st_dev != os.stat("..", dir_fd=dir_fd).st_dev
    except OSError:
        return False


def _under(path: str, base: str) -> bool:
    return path == base or path.startswith(base + os.sep)


def root_text(root: Path) -> str:

    try:
        parent = fd_path(sandboxes_fd(Path(root).parent))
    except OSError:
        parent = os.path.realpath(Path(root).parent)
    return os.path.join(parent, Path(root).name)


def live_trees() -> list[str]:

    return [os.path.abspath(LB_HOME), os.path.abspath(LIVE_KEY_DIR_LEGACY)]


class LiveInodes:


    def __init__(self) -> None:
        self.ids: set[tuple[int, int]] = set()
        self.dirs: dict[str, tuple[int, int, int]] = {}
        self.started_ns: int | None = None
        self.skip = {os.path.abspath(SANDBOXES)}

    def _list(self, path: str) -> list[tuple[str, os.stat_result]]:

        new = []
        try:
            with os.scandir(path) as listing:
                entries = [entry.path for entry in listing]
        except OSError:
            return new
        for child in entries:
            try:
                info = os.stat(child, follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                self.ids.add((info.st_dev, info.st_ino))
            elif stat.S_ISDIR(info.st_mode) and os.path.basename(child) != ".venv" and child not in self.skip:
                if child not in self.dirs:
                    new.append((child, info))
        return new

    def _add_tree(self, path: str, info: os.stat_result) -> None:
        stack = [(path, info)]
        while stack:
            here, seen = stack.pop()

            self.dirs[here] = (seen.st_dev, seen.st_ino, seen.st_mtime_ns)
            stack.extend(self._list(here))

    def refresh(self) -> None:
        self.started_ns = time.time_ns()
        try:
            info = os.stat(LIVE_PLIST, follow_symlinks=False)
            if stat.S_ISREG(info.st_mode):
                self.ids.add((info.st_dev, info.st_ino))
        except OSError:
            pass
        for path, (dev, ino, mtime_ns) in list(self.dirs.items()):
            try:
                info = os.stat(path, follow_symlinks=False)
            except OSError:
                del self.dirs[path]
                continue
            if not stat.S_ISDIR(info.st_mode):
                del self.dirs[path]
            elif (info.st_dev, info.st_ino) != (dev, ino):
                del self.dirs[path]
                self._add_tree(path, info)
            elif info.st_mtime_ns != mtime_ns:
                self.dirs[path] = (dev, ino, info.st_mtime_ns)
                for sub, sub_info in self._list(path):
                    self._add_tree(sub, sub_info)
        for top in live_trees():
            if top in self.dirs:
                continue
            try:
                info = os.stat(top, follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISDIR(info.st_mode):
                self._add_tree(top, info)


def system_links_resolved(text: str) -> str:

    parts = list(Path(os.path.abspath(text)).parts[1:])
    done = "/"
    hops = 0
    while parts:
        part = parts.pop(0)
        candidate = os.path.join(done, part)
        try:
            info = os.lstat(candidate)
        except (FileNotFoundError, NotADirectoryError):
            return os.path.join(candidate, *parts)
        if not stat.S_ISLNK(info.st_mode):
            done = candidate
            continue
        hops += 1
        if info.st_uid != 0 or hops > 32:
            raise Confinement(errno.ELOOP, f"{candidate} is a link this user could have made; not followed")
        target = os.readlink(candidate)
        rest = Path(os.path.normpath(os.path.join(done, target))).parts[1:]
        parts = [*rest, *parts]
        done = "/"
    return done


def _open_top(top: Path, root: Path | None) -> tuple[int, str]:

    text = os.path.abspath(top)
    parent, name = os.path.split(text)
    resolved = os.path.join(system_links_resolved(parent), name) if name else system_links_resolved(text)
    if root is not None:
        base = root_text(root)
        for candidate in (text, resolved):
            if _under(candidate, base):
                rel = Path(os.path.relpath(candidate, base))
                if rel == Path("."):
                    return open_root(root), base
                dir_fd = open_dir(root, rel.parent)
                try:
                    return os.open(rel.name, READ_FLAGS, dir_fd=dir_fd), candidate
                finally:
                    os.close(dir_fd)
    if not name:
        return os.open(resolved, os.O_RDONLY | os.O_DIRECTORY), resolved
    parent_fd = os.open(os.path.dirname(resolved), os.O_RDONLY | os.O_DIRECTORY | (NOFOLLOW_ANY or os.O_NOFOLLOW))
    try:
        return os.open(name, READ_FLAGS, dir_fd=parent_fd), resolved
    finally:
        os.close(parent_fd)


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
        return any(_under(path, e) for e in excluded)

    def checked_file(fd: int, path: str, expect: tuple[int, int] | None):
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (expect is not None and (info.st_dev, info.st_ino) != expect):
            unreadable.append(path)
            return None
        if info.st_nlink != 1:
            if (base is not None and _under(path, base)) or any(_under(path, t) for t in trees):
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
            fd, text = _open_top(Path(top), root)
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
