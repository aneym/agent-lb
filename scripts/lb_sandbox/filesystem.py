from __future__ import annotations

import errno
import json
import os
import stat
import uuid
from pathlib import Path

from .paths import (
    DIR_FLAGS,
    Confinement,
    Refused,
)
from .private_storage import (
    sandboxes_fd,
)


def open_root(root: Path) -> int:

    root = Path(root)
    return os.open(root.name, DIR_FLAGS, dir_fd=sandboxes_fd(root.parent))


def open_dir(root: Path, rel: str | Path = "", create: bool = False) -> int:

    fd = open_root(root)
    try:
        for part in Path(rel).parts:
            if part in (".", "..") or "/" in part:
                raise Confinement(f"unsafe path component {part!r}")
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def single_link(fd: int, what: object, reading: bool = False) -> int:

    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1 or (info.st_nlink == 0 and not reading):
        os.close(fd)
        raise Confinement(f"{what} is not a single-link regular file")
    return fd


def open_in_root(root: Path, rel: str | Path, flags: int, mode: int = 0o600, create_dirs: bool = False) -> int:

    rel = Path(rel)
    dir_fd = open_dir(root, rel.parent, create=create_dirs)
    try:
        fd = os.open(rel.name, (flags & ~os.O_TRUNC) | os.O_NOFOLLOW | os.O_NONBLOCK, mode, dir_fd=dir_fd)
    finally:
        os.close(dir_fd)
    single_link(fd, rel, reading=flags & os.O_ACCMODE == os.O_RDONLY)
    if flags & os.O_TRUNC:
        os.ftruncate(fd, 0)
    return fd


def write_in_root(root: Path, rel: str | Path, data: bytes, mode: int = 0o600) -> None:

    rel = Path(rel)
    dir_fd = open_dir(root, rel.parent, create=True)
    tmp = f".{rel.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=dir_fd)
        try:
            os.fchmod(fd, mode)
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view) :]
        finally:
            os.close(fd)
        os.replace(tmp, rel.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dir_fd)
        except OSError:
            pass
        raise
    finally:
        os.close(dir_fd)


def append_in_root(root: Path, rel: str | Path):

    fd = open_in_root(root, rel, os.O_WRONLY | os.O_APPEND | os.O_CREAT, create_dirs=True)
    return os.fdopen(fd, "a")


def confined(action):

    try:
        return action()
    except OSError as exc:
        if isinstance(exc, Confinement) or exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise Refused(f"sandbox path is a link or shared file: {exc}") from None
        raise


def write_json(root: Path, rel: str | Path, data: object, mode: int = 0o600) -> None:
    write_in_root(root, rel, (json.dumps(data, indent=1, sort_keys=True) + "\n").encode(), mode)


def read_json(path: Path) -> dict:

    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def read_root_json(root: Path, rel: str | Path) -> dict:

    try:
        fd = open_in_root(root, rel, os.O_RDONLY)
    except OSError:
        return {}
    try:
        with os.fdopen(fd, "rb") as fh:
            data = json.loads(fh.read(1 << 20) or b"null")
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def read_root_text(root: Path, rel: str | Path, limit: int) -> str:

    try:
        fd = open_in_root(root, rel, os.O_RDONLY)
    except OSError:
        return ""
    with os.fdopen(fd, "rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        fh.seek(max(0, size - limit))
        return fh.read(limit).decode(errors="replace")
