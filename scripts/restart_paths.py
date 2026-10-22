from __future__ import annotations

import os
import stat
from pathlib import Path

from scripts.restart_context import RestartContext, SandboxRefused


def real_home(context: RestartContext) -> Path:
    import pwd

    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def inside(context: RestartContext, path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def lexical(context: RestartContext, path: Path) -> Path:
    return Path(os.path.normpath(str(path)))


def refuse_links_on(context: RestartContext, name: str, path: Path, leaf: str) -> None:
    parts = path.parts
    for i in range(1, len(parts) + 1):
        prefix = Path(*parts[:i])
        try:
            info = prefix.lstat()
        except FileNotFoundError:
            return
        if i < len(parts) or leaf == "dir":
            if not stat.S_ISDIR(info.st_mode):
                raise SandboxRefused(f"{name} {path}: {prefix} is a link or not a directory")
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SandboxRefused(f"{name} {path} is not a single-link regular file (a link to live state?)")


def trusted_file(context: RestartContext, what: str, path: Path, info: os.stat_result) -> None:
    if info.st_uid not in (os.getuid(), 0) or info.st_mode & 18:
        raise SandboxRefused(f"{what} {path} is one another user could write")


def open_dir_nofollow(context: RestartContext, path: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def check_owned_dir(context: RestartContext, fd: int, what: str, path: Path, private: bool) -> os.stat_result:
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise SandboxRefused(f"{what} {path} is not a directory owned by uid {os.getuid()}")
    if info.st_mode & (63 if private else 18):
        raise SandboxRefused(f"{what} {path} has mode {stat.S_IMODE(info.st_mode):o}; lb-sandbox makes it 0700")
    return info


def root_on_own_volume(context: RestartContext, root_info: os.stat_result, sandboxes_info: os.stat_result) -> bool:
    return root_info.st_dev != sandboxes_info.st_dev


def open_sandbox_root(context: RestartContext, root: Path) -> tuple[int, os.stat_result]:
    try:
        parent = open_dir_nofollow(context, root.parent)
    except OSError as exc:
        raise SandboxRefused(f"sandboxes dir {root.parent} or an ancestor is a link or missing ({exc})") from exc
    try:
        parent_info = check_owned_dir(context, parent, "sandboxes dir", root.parent, private=False)
        try:
            fd = os.open(root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        except OSError as exc:
            raise SandboxRefused(f"sandbox root {root} is a link or missing ({exc})") from exc
    finally:
        os.close(parent)
    try:
        info = check_owned_dir(context, fd, "sandbox root", root, private=True)
        if not root_on_own_volume(context, info, parent_info):
            raise SandboxRefused(
                f"sandbox root {root} is not its own volume (lb-sandbox mounts one at every root); refusing to use it"
            )
    except BaseException:
        os.close(fd)
        raise
    return (fd, info)
