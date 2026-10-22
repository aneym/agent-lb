from __future__ import annotations

import base64
import os
import plistlib
import sqlite3
import stat
from pathlib import Path

from .filesystem import (
    open_in_root,
    open_root,
)
from .paths import (
    CUSTODY_FILES,
    DIR_FLAGS,
    LIVE_PLIST,
    TOKEN_ENV,
    Refused,
)
from .private_storage import (
    Private,
    identity,
    root_entry,
)
from .volumes import (
    on_run_volume,
)


def live_federation_token(plist: Path | None = None) -> str | None:

    with (plist or LIVE_PLIST).open("rb") as fh:
        env = plistlib.load(fh).get("EnvironmentVariables", {})
    value = env.get(TOKEN_ENV)
    return str(value) if value else None


def open_data_dir(root: Path, expect: tuple[int, int] | list[int] | None = None) -> int:

    root_fd = open_root(root)
    try:
        try:
            fd = os.open("data", DIR_FLAGS, dir_fd=root_fd)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise Refused(f"sandbox data dir is not a real directory ({exc.__class__.__name__})") from None
        info, top = os.fstat(fd), os.fstat(root_fd)
    finally:
        os.close(root_fd)
    if info.st_dev != top.st_dev or info.st_uid != os.getuid():
        os.close(fd)
        raise Refused("sandbox data dir is not this user's own on the root's volume")
    if expect is not None and (info.st_dev, info.st_ino) != tuple(expect):
        os.close(fd)
        raise Refused("sandbox data dir changed since it was checked")
    return fd


def check_data_custody_fd(data_fd: int) -> None:

    for name in [Path(rel).name for rel in CUSTODY_FILES] + ["federation-push.json"]:
        try:
            info = os.stat(name, dir_fd=data_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise Refused(f"sandbox data/{name} is not a private single-link file")
    try:
        info = os.stat("conversation-archive", dir_fd=data_fd, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            raise Refused("sandbox data/conversation-archive is not a real directory")
    except FileNotFoundError:
        pass


def check_data_custody(root: Path) -> None:

    try:
        data_fd = open_data_dir(root)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise Refused(f"sandbox data dir is not a real directory under the root ({exc.__class__.__name__})") from None
    try:
        check_data_custody_fd(data_fd)
    finally:
        os.close(data_fd)


def store_rows(root: Path) -> list[list]:

    data_fd = open_data_dir(root)
    here = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
    try:
        check_data_custody_fd(data_fd)
        os.fchdir(data_fd)
        con = sqlite3.connect("file:store.db?mode=ro", uri=True, timeout=5)
        try:
            rows = con.execute(
                "SELECT id, provider, status, access_expires_at, access_token_encrypted, id_token_encrypted,"
                " reset_at, blocked_at, refresh_token_encrypted FROM accounts"
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return []
            raise
        finally:
            con.close()
    finally:
        os.fchdir(here)
        os.close(here)
        os.close(data_fd)
    return [
        [{"b64": base64.b64encode(bytes(v)).decode()} if isinstance(v, (bytes, memoryview)) else v for v in row]
        for row in rows
    ]


def read_key(root: Path) -> str | None:

    try:
        fd = open_in_root(root, "data/encryption.key", os.O_RDONLY)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as fh:
        return fh.read().strip().decode("ascii", "replace") or None


def create_key(dir_fd: int, name: str = "encryption.key") -> list[int]:

    from cryptography.fernet import Fernet

    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dir_fd)
    with os.fdopen(fd, "wb") as fh:
        ident = identity(os.fstat(fh.fileno()))
        fh.write(Fernet.generate_key())
    return ident


def create_run_key(root: Path, priv: Private) -> None:

    if not on_run_volume(root, priv.ids):
        raise Refused(f"sandbox root {root} is not the volume this start attached; no key made")
    data_fd = open_data_dir(root)
    try:
        if os.fstat(data_fd).st_dev != priv.ids["volume"]["st_dev"]:
            raise Refused("sandbox data dir is not on the run's volume; no key made")
        priv.record("key", create_key(data_fd))
    finally:
        os.close(data_fd)


def custody_paths(root: Path) -> list[Path]:

    return [root / rel for rel in CUSTODY_FILES]


def entry_of(root: Path) -> str | None:

    try:
        return root_entry(root)
    except FileNotFoundError:
        return None
    except OSError:
        return "unknown"
