from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import time
from pathlib import Path

from .paths import (
    DIR_FLAGS,
    SANDBOXES,
    Confinement,
    Refused,
    check_run_id,
)


def check_root(root: Path, sandboxes: Path | None = None) -> Path:
    base = Path(os.path.normpath(os.path.abspath(sandboxes or SANDBOXES)))
    path = Path(os.path.normpath(os.path.abspath(root)))
    if path.parent != base:
        raise Refused(f"sandbox root {path} is not directly under {base}")
    check_run_id(path.name)
    try:
        sb = sandboxes_fd(base)
    except FileNotFoundError:
        return path
    except OSError as exc:
        raise Refused(f"sandboxes dir {base} is a link or not private ({exc})") from None
    try:
        info = os.stat(path.name, dir_fd=sb, follow_symlinks=False)
    except FileNotFoundError:
        return path
    if not stat.S_ISDIR(info.st_mode):
        raise Refused(f"sandbox root {path} is a link or not a directory")
    return path


_PINNED: dict[str, tuple[int, tuple[int, int]]] = {}


def _open_dir_no_links(text: str) -> int:
    nofollow_any = getattr(os, "O_NOFOLLOW_ANY", 0)
    if nofollow_any:
        return os.open(text, os.O_RDONLY | os.O_DIRECTORY | nofollow_any)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(text).parts[1:]:
            child = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def sandboxes_fd(base: Path | None = None, create: bool = False) -> int:
    text = os.path.normpath(os.path.abspath(base or SANDBOXES))
    if text in _PINNED:
        return _PINNED[text][0]
    if create:
        os.makedirs(text, mode=0o700, exist_ok=True)
    fd = _open_dir_no_links(text)
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        os.close(fd)
        raise Confinement(f"sandboxes dir {text} is not this user's own private directory")
    if create:
        os.fchmod(fd, 0o700)
    _PINNED[text] = (fd, (info.st_dev, info.st_ino))
    return fd


def sandboxes_id(base: Path | None = None) -> tuple[int, int]:
    sandboxes_fd(base)
    return _PINNED[os.path.normpath(os.path.abspath(base or SANDBOXES))][1]


def fd_path(fd: int) -> str:
    if sys.platform == "darwin":
        import fcntl

        return os.fsdecode(fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024)).split(b"\0", 1)[0])
    return os.readlink(f"/proc/self/fd/{fd}")


PRIVATE_PREFIX = ".lbsbx."


PRIVATE_NAME_RE = re.compile(r"^\.lbsbx\.[0-9a-f]{24}\.[0-9a-f]{32}$")


RECORD_NAME = "ids.jsonl"


IMAGE_NAME = "volume.sparseimage"


ROOT_STAGE = "root"


ROOT_STAGE_RE = re.compile(r"^root(\.[0-9a-f]{32})?$")


RENAME_EXCL = 0x00000004


RENAME_NOREPLACE = 0x00000001


BIRTH_SLACK_S = 0.01


MADE_DIR_MODE = 0o700


def identity(info: os.stat_result) -> list[int]:
    birth = getattr(info, "st_birthtime_ns", None)
    if birth is None:
        seconds = getattr(info, "st_birthtime", None)
        birth = round(seconds * 1e9) if seconds is not None else 0
    return [info.st_dev, info.st_ino, int(birth)]


def born(info: os.stat_result) -> float:
    return getattr(info, "st_birthtime", info.st_ctime)


def rename_excl(src_dir: int, src: str, dst_dir: int, dst: str) -> None:
    import ctypes

    if sys.platform == "darwin":
        fn = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True).renameatx_np
        flags = RENAME_EXCL
    else:
        fn = ctypes.CDLL(None, use_errno=True).renameat2
        flags = RENAME_NOREPLACE
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    fn.restype = ctypes.c_int
    if fn(src_dir, os.fsencode(src), dst_dir, os.fsencode(dst), flags) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), dst)


def private_prefix(run_id: str) -> str:

    return f"{PRIVATE_PREFIX}{hashlib.sha256(check_run_id(run_id).encode()).hexdigest()[:24]}."


def fresh_dir(fd: int, parent_fd: int, made_after: float, what: str) -> os.stat_result:

    info = os.fstat(fd)
    with os.scandir(fd) as listing:
        empty = next(iter(listing), None) is None
    if not (
        stat.S_ISDIR(info.st_mode)
        and stat.S_IMODE(info.st_mode) == MADE_DIR_MODE
        and info.st_uid == os.getuid()
        and info.st_dev == os.fstat(parent_fd).st_dev
        and info.st_nlink == 2
        and empty
        and born(info) >= made_after - BIRTH_SLACK_S
    ):
        raise Refused(f"the {what} start just made was swapped before use; nothing of it is owned")
    return info


class Private:
    def __init__(self, sb: int, name: str, fd: int, ident: list[int], ids: dict, record_id: list[int]) -> None:
        self.sb, self.name, self.fd, self.ident, self.ids, self.record_id = sb, name, fd, ident, ids, record_id
        self.record_fd: int | None = None

    def record(self, what: str, value: object) -> None:
        if self.record_fd is None:
            raise Refused("only the start that made the private dir records into it")
        os.write(self.record_fd, (json.dumps({"what": what, "value": value}, sort_keys=True) + "\n").encode())
        os.fsync(self.record_fd)
        add_record(self.ids, what, value)

    def close(self) -> None:
        for fd in (self.record_fd, self.fd):
            if fd is not None and fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self.record_fd, self.fd = None, -1


def add_record(ids: dict, what: str, value: object) -> None:
    if what == "job":
        if isinstance(value, dict) and isinstance(value.get("name"), str):
            ids.setdefault("jobs", []).append(value)
    elif what == "stage":
        if isinstance(value, str) and ROOT_STAGE_RE.match(value):
            ids.setdefault("stage", value)
    elif what in ("private", "root", "image", "key", "volume"):
        ids.setdefault(what, value)


def make_private(run_id: str) -> Private:

    import secrets as secrets_module

    sb = sandboxes_fd()
    made_after = time.time()
    name = private_prefix(run_id) + secrets_module.token_hex(16)
    os.mkdir(name, 0o700, dir_fd=sb)
    fd = os.open(name, DIR_FLAGS, dir_fd=sb)
    try:
        info = fresh_dir(fd, sb, made_after, "private dir")
        record_fd = os.open(
            RECORD_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_APPEND, 0o600, dir_fd=fd
        )
    except BaseException:
        os.close(fd)
        raise
    priv = Private(sb, name, fd, identity(info), {}, identity(os.fstat(record_fd)))
    priv.record_fd = record_fd
    priv.record("private", priv.ident)
    return priv


def private_names(run_id: str) -> list[str]:
    prefix = private_prefix(run_id)
    with os.scandir(sandboxes_fd()) as listing:
        return sorted(e.name for e in listing if e.name.startswith(prefix) and PRIVATE_NAME_RE.match(e.name))


def open_private(run_id: str) -> Private | None:

    names = private_names(run_id)
    if not names:
        return None
    if len(names) > 1:
        raise Refused(f"run {run_id} has {len(names)} private dirs; refusing to pick one")
    sb = sandboxes_fd()
    fd = os.open(names[0], DIR_FLAGS, dir_fd=sb)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_dev != os.fstat(sb).st_dev or stat.S_IMODE(info.st_mode) & 0o077:
            raise Refused(f"the private dir of run {run_id} is not this user's private directory")
        record = os.open(RECORD_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            rinfo = os.fstat(record)
            if not stat.S_ISREG(rinfo.st_mode) or rinfo.st_nlink != 1 or rinfo.st_uid != os.getuid():
                raise Refused(f"the record of run {run_id} is not a private single-link file")
            data = os.read(record, 1 << 20)
        finally:
            os.close(record)
        ids: dict = {}
        for line in data.decode(errors="replace").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                add_record(ids, str(entry.get("what")), entry.get("value"))
        if ids.get("private") != identity(info):
            raise Refused(f"the record of run {run_id} names another directory")
    except BaseException:
        os.close(fd)
        raise
    return Private(sb, names[0], fd, identity(info), ids, identity(rinfo))


def remove_recorded(dir_fd: int, name: str, ident: list[int] | None, kind: str, into: int | None = None) -> bool | None:

    import secrets as secrets_module

    stage = dir_fd if into is None else into
    aside = f".rm.{secrets_module.token_hex(16)}"
    try:
        rename_excl(dir_fd, name, stage, aside)
    except FileNotFoundError:
        return None
    except OSError:
        return False
    removed = False
    flags = (os.O_RDONLY | os.O_DIRECTORY) if kind == "dir" else os.O_RDWR
    try:
        fd = os.open(aside, flags | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=stage)
    except OSError:
        fd = -1
    if fd >= 0:
        try:
            info = os.fstat(fd)
            shape = stat.S_ISDIR(info.st_mode) if kind == "dir" else stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            ours = ident is not None and shape and identity(info) == list(ident)
            if ours and kind != "dir":
                os.ftruncate(fd, 0)
            if ours and identity(os.stat(aside, dir_fd=stage, follow_symlinks=False)) == list(ident):
                if kind == "dir":
                    os.rmdir(aside, dir_fd=stage)
                else:
                    os.unlink(aside, dir_fd=stage)
                removed = True
        except OSError:
            removed = False
        finally:
            os.close(fd)
    if not removed:
        try:
            rename_excl(stage, aside, dir_fd, name)
        except OSError:
            pass
    return removed


def remove_private(priv: Private) -> bool:

    try:
        with os.scandir(priv.fd) as listing:
            left = sorted(entry.name for entry in listing)
    except OSError:
        return False
    if left not in ([RECORD_NAME], []):
        return False
    if left and remove_recorded(priv.fd, RECORD_NAME, priv.record_id, "file") is False:
        return False
    try:
        if identity(os.stat(priv.name, dir_fd=priv.sb, follow_symlinks=False)) != priv.ident:
            return False
        os.rmdir(priv.name, dir_fd=priv.sb)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def root_stage(ids: dict) -> str:

    stage = ids.get("stage")
    return stage if isinstance(stage, str) and ROOT_STAGE_RE.match(stage) else ROOT_STAGE


def make_root(root: Path, priv: Private) -> tuple[int, list[int]]:

    import secrets as secrets_module

    stage = f"{ROOT_STAGE}.{secrets_module.token_hex(16)}"
    priv.record("stage", stage)
    made_after = time.time()
    os.mkdir(stage, MADE_DIR_MODE, dir_fd=priv.fd)
    fd = os.open(stage, DIR_FLAGS, dir_fd=priv.fd)
    try:
        ident = identity(fresh_dir(fd, priv.fd, made_after, "sandbox root"))
        priv.record("root", ident)
        try:
            rename_excl(priv.fd, stage, priv.sb, Path(root).name)
        except FileExistsError:
            raise Refused(f"sandbox root {root} already exists") from None
    except BaseException:
        os.close(fd)
        raise
    return fd, ident


def root_entry(root: Path) -> str | None:

    root = Path(root)
    try:
        info = os.stat(root.name, dir_fd=sandboxes_fd(root.parent), follow_symlinks=False)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode):
        return "link"
    return "dir" if stat.S_ISDIR(info.st_mode) else "other"
