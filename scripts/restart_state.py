from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import plistlib
import shutil
import stat
import sys
import time
from pathlib import Path
from typing import cast

from scripts.restart_context import RestartContext, SandboxRefused
from scripts.restart_paths import lexical, open_dir_nofollow, open_sandbox_root
from scripts.restart_sandbox import (
    check_sandbox_bootstrap,
    check_sandbox_interpreter,
    check_sandbox_plist,
    read_sandbox_config,
    sandbox_bindings,
    sandbox_python_for,
)


def apply_sandbox(context: RestartContext, config_path: Path) -> None:
    unpin_sandbox_root(context)
    config, root_fd, root_info = read_sandbox_config(context, config_path)
    try:
        bindings = sandbox_bindings(context, config)
    except BaseException:
        os.close(root_fd)
        raise
    context.__dict__.update(bindings)
    context.SANDBOX_ROOT_FD, context.SANDBOX_ROOT_PINNED = (
        root_fd,
        (cast(Path, bindings["SANDBOX_ROOT"]), root_info.st_dev, root_info.st_ino),
    )
    try:
        pin_sandbox_root(context)
        with os.fdopen(sandbox_open(context, cast(Path, bindings["PLIST"]), os.O_RDONLY), "rb") as fh:
            plist = plistlib.load(fh)
        check_sandbox_plist(
            context,
            plist,
            cast(Path, bindings["SANDBOX_ROOT"]),
            cast(str, bindings["LABEL"]),
            cast(int, bindings["PRIMARY_PORT"]),
        )
        check_sandbox_bootstrap(context, cast(Path, bindings["SANDBOX_ROOT"]))
        check_sandbox_interpreter(
            context,
            sandbox_python_for(context, cast(Path, bindings["SANDBOX_ROOT"])),
            cast(Path, bindings["SANDBOX_ROOT"]),
        )
    except BaseException as exc:
        context.__dict__.update(context.LIVE_BINDINGS)
        unpin_sandbox_root(context)
        if isinstance(exc, (OSError, ValueError, plistlib.InvalidFileException)):
            raise SandboxRefused(f"cannot read sandbox plist: {exc.__class__.__name__}") from exc
        raise
    context.SANDBOX_CONFIG = lexical(context, Path(config_path).expanduser().absolute())


def pin_sandbox_root(context: RestartContext) -> int:
    assert context.SANDBOX_ROOT is not None
    if (
        context.SANDBOX_ROOT_FD is not None
        and context.SANDBOX_ROOT_PINNED is not None
        and (context.SANDBOX_ROOT_PINNED[0] == context.SANDBOX_ROOT)
    ):
        return context.SANDBOX_ROOT_FD
    unpin_sandbox_root(context)
    fd, info = open_sandbox_root(context, context.SANDBOX_ROOT)
    context.SANDBOX_ROOT_FD, context.SANDBOX_ROOT_PINNED = (fd, (context.SANDBOX_ROOT, info.st_dev, info.st_ino))
    return fd


def unpin_sandbox_root(context: RestartContext) -> None:
    if context.SANDBOX_ROOT_FD is not None:
        os.close(context.SANDBOX_ROOT_FD)
    context.SANDBOX_ROOT_FD, context.SANDBOX_ROOT_PINNED = (None, None)


def verify_sandbox_ancestry(context: RestartContext) -> None:
    fd = pin_sandbox_root(context)
    try:
        again = open_dir_nofollow(context, cast(Path, context.SANDBOX_ROOT))
    except OSError as exc:
        raise SandboxRefused(f"sandbox root {context.SANDBOX_ROOT} or an ancestor became a link ({exc})") from exc
    try:
        now, pinned = (os.fstat(again), os.fstat(fd))
    finally:
        os.close(again)
    if (now.st_dev, now.st_ino) != (pinned.st_dev, pinned.st_ino):
        raise SandboxRefused(f"sandbox root {context.SANDBOX_ROOT} no longer names the directory this run pinned")


def sandbox_dir_fd(context: RestartContext, directory: Path, create: bool = True) -> int:
    assert context.SANDBOX_ROOT is not None
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        rel = directory.relative_to(context.SANDBOX_ROOT)
    except ValueError as exc:
        raise SandboxRefused(f"{directory} is outside the sandbox root") from exc
    if ".." in rel.parts:
        raise SandboxRefused(f"{directory} climbs out of the sandbox root")
    verify_sandbox_ancestry(context)
    fd = os.dup(pin_sandbox_root(context))
    try:
        for part in rel.parts:
            if create:
                try:
                    os.mkdir(part, 448, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
            if os.fstat(fd).st_uid != os.getuid():
                raise SandboxRefused(f"{directory}: {part} is not owned by uid {os.getuid()}")
        return fd
    except BaseException:
        os.close(fd)
        raise


def open_in(context: RestartContext, dir_fd: int, path: Path, flags: int, mode: int = 384) -> int:
    try:
        fd = os.open(path.name, flags & ~os.O_TRUNC | os.O_NOFOLLOW, mode, dir_fd=dir_fd)
    except OSError as exc:
        raise SandboxRefused(f"{path} is a link or unreachable inside the sandbox root ({exc})") from exc
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd)
        raise SandboxRefused(f"{path} is not a single-link regular file; refusing to write through it")
    if flags & os.O_TRUNC:
        os.ftruncate(fd, 0)
    return fd


def state_dir_fd(context: RestartContext, directory: Path, create: bool = True) -> int:
    try:
        return sandbox_dir_fd(context, directory, create=create)
    except SandboxRefused:
        raise
    except OSError as exc:
        raise SandboxRefused(f"{directory} is a link or unreachable inside the sandbox root ({exc})") from exc


def same_dir_still(context: RestartContext, dir_fd: int, directory: Path) -> None:
    again = state_dir_fd(context, directory, create=False)
    try:
        now, held = (os.fstat(again), os.fstat(dir_fd))
    finally:
        os.close(again)
    if (now.st_dev, now.st_ino) != (held.st_dev, held.st_ino):
        raise SandboxRefused(f"{directory} was swapped during the operation; refusing to continue")


def sandbox_open(context: RestartContext, path: Path, flags: int, mode: int = 384) -> int:
    dir_fd = state_dir_fd(context, path.parent)
    try:
        return open_in(context, dir_fd, path, flags, mode)
    finally:
        os.close(dir_fd)


def read_state(context: RestartContext, path: Path) -> tuple[str, float] | None:
    if context.SANDBOX_ROOT is None:
        try:
            return (path.read_text(), path.stat().st_mtime)
        except OSError:
            return None
    try:
        dir_fd = sandbox_dir_fd(context, path.parent, create=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SandboxRefused(f"{path.parent} is a link or unreachable inside the sandbox root ({exc})") from exc
    try:
        for attempt in range(5):
            try:
                fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise SandboxRefused(f"{path} is a link inside the sandbox root ({exc})") from exc
            with os.fdopen(fd, "rb") as fh:
                info = os.fstat(fh.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                    raise SandboxRefused(f"{path} is not a single-link regular file")
                text = fh.read().decode(errors="replace")
            if info.st_nlink == 1 or attempt == 4:
                return (text, info.st_mtime)
    finally:
        os.close(dir_fd)
    return None


def remove_state(context: RestartContext, path: Path) -> None:
    if context.SANDBOX_ROOT is None:
        path.unlink(missing_ok=True)
        return
    dir_fd = state_dir_fd(context, path.parent)
    try:
        try:
            info = os.stat(path.name, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SandboxRefused(f"{path} is not a single-link regular file; refusing to remove it")
        try:
            os.unlink(path.name, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
        same_dir_still(context, dir_fd, path.parent)
    finally:
        os.close(dir_fd)


def ensure_dir(context: RestartContext, path: Path) -> None:
    if context.SANDBOX_ROOT is None:
        path.mkdir(parents=True, exist_ok=True)


def open_append(context: RestartContext, path: Path):
    if context.SANDBOX_ROOT is None:
        return path.open("a")
    return os.fdopen(sandbox_open(context, path, os.O_WRONLY | os.O_APPEND | os.O_CREAT), "a")


def write_state(context: RestartContext, path: Path, text: str) -> None:
    if context.SANDBOX_ROOT is None:
        path.write_text(text)
        return
    with os.fdopen(sandbox_open(context, path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC), "w") as fh:
        fh.write(text)


def read_plist(context: RestartContext) -> dict:
    if context.SANDBOX_ROOT is None:
        with context.PLIST.open("rb") as fh:
            return plistlib.load(fh)
    with os.fdopen(sandbox_open(context, context.PLIST, os.O_RDONLY), "rb") as fh:
        data = plistlib.load(fh)
    check_sandbox_plist(context, data, context.SANDBOX_ROOT, context.LABEL, context.PRIMARY_PORT)
    check_sandbox_bootstrap(context, context.SANDBOX_ROOT)
    return data


def now_iso(context: RestartContext) -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def say(context: RestartContext, msg: str) -> None:
    print(f"[lb-restart +{time.monotonic() - context.T0:6.1f}s] {msg}", flush=True)


def sync_log(context: RestartContext, line: str) -> None:
    with open_append(context, context.SYNC_LOG) as fh:
        fh.write(f"{now_iso(context)} {line}\n")


def set_preferred(context: RestartContext, port: int) -> None:
    tmp = context.PREFERRED_FILE.with_suffix(".tmp")
    if context.SANDBOX_ROOT is None:
        context.STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp.write_text(f"{port}\n")
        os.replace(tmp, context.PREFERRED_FILE)
        return
    if tmp.parent != context.PREFERRED_FILE.parent:
        raise SandboxRefused("preference temp file is not beside the preference file")
    dir_fd = state_dir_fd(context, context.PREFERRED_FILE.parent)
    try:
        with os.fdopen(open_in(context, dir_fd, tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 420), "w") as fh:
            fh.write(f"{port}\n")
        os.replace(tmp.name, context.PREFERRED_FILE.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        same_dir_still(context, dir_fd, context.PREFERRED_FILE.parent)
    finally:
        os.close(dir_fd)


class Lock:
    def __init__(self, context: RestartContext, reason: str, wait: float) -> None:
        self.context = context
        self.reason = reason
        self.wait = wait
        self.fh = None

    def __enter__(self) -> "Lock":
        ensure_dir(self.context, self.context.LOCK_FILE.parent)
        if self.context.SANDBOX_ROOT is None:
            self.fh = self.context.LOCK_FILE.open("a+")
        else:
            self.fh = os.fdopen(
                sandbox_open(self.context, self.context.LOCK_FILE, os.O_RDWR | os.O_APPEND | os.O_CREAT), "a+"
            )
        deadline = time.monotonic() + self.wait
        announced = False
        while True:
            try:
                fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if not announced:
                    self.fh.seek(0)
                    say(
                        self.context, f"another restart holds the lock ({self.fh.read().strip() or 'unknown'}); waiting"
                    )
                    announced = True
                if time.monotonic() >= deadline:
                    say(self.context, f"lock not free after {self.wait:.0f}s; retry later")
                    sys.exit(75)
                time.sleep(1)
        self.fh.seek(0)
        self.fh.truncate()
        self.fh.write(f"pid={os.getpid()} since={now_iso(self.context)} reason={self.reason}\n")
        self.fh.flush()
        return self

    def __exit__(self, *exc) -> None:
        assert self.fh is not None
        self.fh.seek(0)
        self.fh.truncate()
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


class Files:
    def __init__(self, context: RestartContext, label: str) -> None:
        self.context = context
        self.backup_dir: Path | None = None
        self.created: list[Path] = []
        self.restored_from: Path | None = None
        self.label = label

    def deploy(self, src_root: Path, rel_paths: list[str]) -> None:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.backup_dir = self.context.BACKUPS / f"{self.label}-{stamp}"
        for rel in rel_paths:
            src = (src_root / rel).resolve()
            dst = self.context.RUNTIME / rel
            if not src.is_file():
                raise SystemExit(f"--files: {src} is not a file")
            if dst.exists():
                bak = self.backup_dir / rel
                bak.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, bak)
            else:
                self.created.append(dst)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        (self.backup_dir / "MANIFEST.json").write_text(
            json.dumps({"from": str(src_root), "files": rel_paths, "created": [str(p) for p in self.created]}, indent=1)
        )
        for rel in rel_paths:
            dst = self.context.RUNTIME / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src_root / rel, dst)
            shutil.copymode(src_root / rel, dst)
            if (src_root / rel).read_bytes() != dst.read_bytes():
                raise SystemExit(f"copy verify failed for {rel}")
        say(self.context, f"deployed {len(rel_paths)} file(s); backup {self.backup_dir}")

    def use_backup(self, backup: Path) -> None:
        self.backup_dir = backup / "runtime" if (backup / "runtime").is_dir() else backup

    def rollback(self) -> bool:
        if self.backup_dir is None or not self.backup_dir.is_dir():
            return False
        restored = 0
        for bak in self.backup_dir.rglob("*"):
            if not bak.is_file() or bak.name == "MANIFEST.json":
                continue
            rel = bak.relative_to(self.backup_dir)
            dst = self.context.RUNTIME / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(bak, dst)
            restored += 1
        for path in self.created:
            path.unlink(missing_ok=True)
        say(
            self.context,
            f"rolled back {restored} file(s) from {self.backup_dir}, removed {len(self.created)} new file(s)",
        )
        self.restored_from = self.backup_dir
        return True
