from __future__ import annotations

import os
from pathlib import Path

from .detection import (
    Detector,
    scan_stream,
)
from .filesystem import (
    open_dir,
    single_link,
)
from .paths import (
    CUSTODY_FILES,
    Confinement,
)
from .private_storage import (
    identity,
    remove_recorded,
)
from .traversal import (
    root_text,
)


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
