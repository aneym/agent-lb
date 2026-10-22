from __future__ import annotations

import errno
import json
import os
import re
import signal
import stat
import sys
import time
import uuid
from pathlib import Path

from scripts.lb_sandbox_guards import (
    DIR_FLAGS,
    IMAGE_NAME,
    LB_HOME,
    LIVE_OLD_KEY_DIR,
    LIVE_PLIST,
    LIVE_RUNTIME,
    MADE_DIR_MODE,
    REAL_HOME,
    RECORD_NAME,
    ROOT_STAGE,
    ROOT_STAGE_RE,
    Confinement,
    Private,
    Refused,
    Unhealthy,
    add_record,
    fd_path,
    fresh_dir,
    identity,
    lexical,
    private_names,
    rename_excl,
    sandboxes_fd,
)


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


def seatbelt_profile(root: Path, reads: bool) -> str:

    def q(path: Path | str, resolve: bool = True) -> str:
        text = os.path.realpath(path) if resolve else str(path)
        if '"' in text or "\\" in text:
            raise Refused(f"path {text!r} cannot go in a sandbox profile")
        return f'"{text}"'

    rules = [
        "(version 1)",
        "(allow default)",
        f"(deny file-write* (subpath {q(REAL_HOME)}))",
    ]
    if reads:
        runtime = Path(os.path.realpath(LIVE_RUNTIME))
        try:
            envs = sorted(name for name in os.listdir(runtime) if name.startswith(".env"))
        except OSError:
            envs = []
        private = " ".join([f"(subpath {q(runtime / '.codex-lb')})"] + [f"(literal {q(runtime / n)})" for n in envs])
        custody = f"(subpath {q(LB_HOME)}) (subpath {q(LIVE_OLD_KEY_DIR)}) (literal {q(LIVE_PLIST)})"
        rules += [
            f"(deny file-read-data {custody})",
            f"(allow file-read-data (subpath {q(runtime)}))",
            f"(deny file-read-data {private})",
        ]

    if os.path.realpath(Path(root).parent) != str(lexical(Path(root).parent)):
        raise Refused(f"sandbox root {root} sits under a linked directory")
    own = Path(fd_path(sandboxes_fd(Path(root).parent))) / Path(root).name
    if reads:
        rules.append(f"(allow file-read-data (literal {q(own.parent, resolve=False)}))")

    rules.append(f"(allow file-read* file-read-data file-write* (subpath {q(own, resolve=False)}))")
    return "".join(rules)


def _libsystem():
    import ctypes

    lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    lib.sandbox_init.argtypes = [ctypes.c_char_p, ctypes.c_uint64, ctypes.POINTER(ctypes.c_char_p)]
    lib.sandbox_init.restype = ctypes.c_int
    lib.sandbox_check.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    lib.sandbox_check.restype = ctypes.c_int
    return lib


def confine(root: Path, reads: bool = True) -> None:

    import ctypes

    if root_entry(root) != "dir":
        raise Refused(f"sandbox root {root} is not a real directory")
    profile = seatbelt_profile(root, reads)
    error = ctypes.c_char_p()
    if _libsystem().sandbox_init(profile.encode(), 0, ctypes.byref(error)) != 0:
        raise Refused(f"cannot confine to {root}: {(error.value or b'').decode(errors='replace')}")


def is_confined(pid: int | None) -> bool | None:

    if pid is None or not pid or sys.platform != "darwin":
        return None
    try:
        return _libsystem().sandbox_check(int(pid), None, 0) == 1  # pyrefly: ignore[bad-argument-type]
    except OSError:
        return None


def run_confined(root: Path, action, reads: bool, timeout_s: int = 120):

    if sys.platform != "darwin":
        return action()
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        code = 1
        try:
            os.close(read_fd)
            for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                signal.signal(sig, signal.SIG_DFL)
            signal.alarm(timeout_s)
            confine(root, reads)
            out: dict = {"ok": action()}
            code = 0
        except BaseException as exc:  # noqa: BLE001 - reported to the parent by class and message
            out = {"error": exc.__class__.__name__, "message": str(exc)}
        try:
            data = json.dumps(out).encode()
            while data:
                data = data[os.write(write_fd, data) :]
        finally:
            os._exit(code)
    os.close(write_fd)
    chunks = []
    with os.fdopen(read_fd, "rb") as fh:
        while chunk := fh.read(65536):
            chunks.append(chunk)
    os.waitpid(pid, 0)
    try:
        out = json.loads(b"".join(chunks) or b"{}")
    except ValueError:
        out = {}
    if "ok" in out:
        return out["ok"]
    error_kind = out.get("error")
    kind = {"Refused": Refused, "Unhealthy": Unhealthy}.get(error_kind, Unhealthy)  # pyrefly: ignore[no-matching-overload]
    raise kind(f"confined step failed: {out.get('error', 'no result')}: {out.get('message', '')}")


def on_own_volume(root: Path) -> bool:

    try:
        sb = sandboxes_fd(Path(root).parent)
        fd = os.open(Path(root).name, DIR_FLAGS, dir_fd=sb)
    except OSError:
        return False
    try:
        return os.fstat(fd).st_dev != os.fstat(sb).st_dev
    finally:
        os.close(fd)


def check_own_volume(root: Path) -> None:
    if not on_own_volume(root):
        raise Refused(f"sandbox root {root} is not its own volume; refusing to work in it")


def statfs_from(fd: int) -> str | None:

    if sys.platform != "darwin":
        return None
    import ctypes

    class StatFS(ctypes.Structure):
        _fields_ = [
            ("f_bsize", ctypes.c_uint32),
            ("f_iosize", ctypes.c_int32),
            ("f_blocks", ctypes.c_uint64),
            ("f_bfree", ctypes.c_uint64),
            ("f_bavail", ctypes.c_uint64),
            ("f_files", ctypes.c_uint64),
            ("f_ffree", ctypes.c_uint64),
            ("f_fsid", ctypes.c_int32 * 2),
            ("f_owner", ctypes.c_uint32),
            ("f_type", ctypes.c_uint32),
            ("f_flags", ctypes.c_uint32),
            ("f_fssubtype", ctypes.c_uint32),
            ("f_fstypename", ctypes.c_char * 16),
            ("f_mntonname", ctypes.c_char * 1024),
            ("f_mntfromname", ctypes.c_char * 1024),
            ("f_flags_ext", ctypes.c_uint32),
            ("f_reserved", ctypes.c_uint32 * 7),
        ]

    lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    info = StatFS()
    if lib.fstatfs(fd, ctypes.byref(info)) != 0:
        return None
    device = info.f_mntfromname.decode(errors="replace")
    return device if re.fullmatch(r"/dev/disk[0-9]+(s[0-9]+)*", device) else None


def volume_device(root: Path) -> str | None:

    try:
        sb = sandboxes_fd(Path(root).parent)
        fd = os.open(Path(root).name, DIR_FLAGS, dir_fd=sb)
    except OSError:
        return None
    try:
        if os.fstat(fd).st_dev == os.fstat(sb).st_dev:
            return None
        return statfs_from(fd)
    finally:
        os.close(fd)


def image_exists(run_id: str) -> bool:

    try:
        priv = open_private(run_id)
    except (OSError, Refused):
        return True
    if priv is None:
        return False
    try:
        os.stat(IMAGE_NAME, dir_fd=priv.fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return True
    finally:
        priv.close()


IMAGE_MAGIC = b"encrcdsa"


def in_dir(fd: int):

    def enter() -> None:
        os.umask(0o077)
        os.fchdir(fd)

    return enter
