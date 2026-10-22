from __future__ import annotations
import os
import plistlib
import re
import stat
import subprocess
import sys
import time
from pathlib import Path
from lb_sandbox_filesystem import (
    open_root,
    write_in_root,
)
from lb_sandbox_paths import (
    Confinement,
    DIR_FLAGS,
    Refused,
    Unhealthy,
    VOLUME_SIZE,
)
from lb_sandbox_private_storage import (
    BIRTH_SLACK_S,
    IMAGE_NAME,
    born,
    fd_path,
    identity,
    open_private,
    sandboxes_fd,
)


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


def _statfs_from(fd: int) -> str | None:

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
        return _statfs_from(fd)
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


def _in_dir(fd: int):


    def enter() -> None:
        os.umask(0o077)
        os.fchdir(fd)

    return enter


def _image_identity(dir_fd: int, name: str, made_after: float) -> list[int]:

    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise Confinement(f"image {name} is not this user's single-link regular file")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise Confinement(f"image {name} is not private; not the image hdiutil made")
        if born(info) < made_after - BIRTH_SLACK_S or os.pread(fd, len(IMAGE_MAGIC), 0) != IMAGE_MAGIC:
            raise Confinement(f"image {name} is not the encrypted image this create made")
        return identity(info)
    finally:
        os.close(fd)


def _same_entry(dir_fd: int, name: str, ident: list[int]) -> bool:
    try:
        info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and identity(info) == list(ident)


def _attached_devices(plist_out: bytes) -> list[str]:
    try:
        entities = plistlib.loads(plist_out).get("system-entities", [])
    except Exception:
        entities = []
    return sorted({e["dev-entry"] for e in entities if isinstance(e.get("dev-entry"), str)}, key=len)


def _detach_attached(plist_out: bytes) -> None:

    for device in _attached_devices(plist_out):
        subprocess.run(["/usr/bin/hdiutil", "detach", "-quiet", "-force", device], capture_output=True, timeout=60)


def attach_volume(root: Path, run_id: str, priv: Private, root_ident: list[int]) -> None:

    import secrets as secrets_module

    passphrase = secrets_module.token_hex(32).encode()
    made_after = time.time()
    create = subprocess.run(
        ["/usr/bin/hdiutil", "create", "-quiet", "-size", VOLUME_SIZE, "-type", "SPARSE", "-fs", "APFS"]
        + ["-volname", "lb-sandbox", "-encryption", "AES-256", "-stdinpass", IMAGE_NAME],
        input=passphrase,
        capture_output=True,
        timeout=180,
        check=False,
        preexec_fn=_in_dir(priv.fd),
    )
    if create.returncode != 0:
        raise Unhealthy(f"hdiutil create failed (exit {create.returncode})")
    try:
        ident = _image_identity(priv.fd, IMAGE_NAME, made_after)
    except OSError as exc:
        raise Refused(f"the new image was swapped before attach ({exc.__class__.__name__}); not attached") from None
    priv.record("image", ident)
    try:
        now = identity(os.stat(Path(root).name, dir_fd=priv.sb, follow_symlinks=False))
    except OSError:
        now = None
    if now != list(root_ident):
        raise Refused(f"sandbox root {root} was swapped before attach; not mounted")
    mountpoint = os.path.join(fd_path(priv.sb), Path(root).name)
    if not _same_entry(priv.fd, IMAGE_NAME, ident):
        raise Refused("the new image was swapped before attach; not attached")
    attach = subprocess.run(
        ["/usr/bin/hdiutil", "attach", "-plist", "-nobrowse", "-noautoopen", "-owners", "on", "-stdinpass"]
        + ["-mountpoint", mountpoint, IMAGE_NAME],
        input=passphrase,
        capture_output=True,
        timeout=120,
        check=False,
        preexec_fn=_in_dir(priv.fd),
    )
    del passphrase
    if attach.returncode != 0:
        raise Unhealthy(f"the sandbox volume did not mount at {root} (hdiutil exit {attach.returncode})")
    device = volume_device(root)
    if not (_same_entry(priv.fd, IMAGE_NAME, ident) and device and device in _attached_devices(attach.stdout)):
        _detach_attached(attach.stdout)
        raise Unhealthy("the sandbox volume did not mount our image at the root")
    fd = open_root(root)
    try:
        info = os.fstat(fd)
        if info.st_dev == os.fstat(priv.sb).st_dev or _statfs_from(fd) != device:

            raise Refused(f"sandbox root {root} is not the volume just attached; not used")
        priv.record("volume", {"st_dev": info.st_dev, "device": device})
        os.fchmod(fd, 0o700)
    finally:
        os.close(fd)
    write_in_root(root, ".metadata_never_index", b"")


def on_run_volume(root: Path, ids: dict) -> bool:

    volume = ids.get("volume") if isinstance(ids.get("volume"), dict) else {}
    want = volume.get("st_dev")
    if type(want) is not int:
        return False
    try:
        sb = sandboxes_fd(Path(root).parent)
        fd = os.open(Path(root).name, DIR_FLAGS, dir_fd=sb)
    except OSError:
        return False
    try:
        dev = os.fstat(fd).st_dev
        return dev == want and dev != os.fstat(sb).st_dev
    finally:
        os.close(fd)


def detach_volume(root: Path) -> dict:

    if not on_own_volume(root):
        return {"volume": "not mounted"}
    for extra in ([], [], ["-force"]):
        device = volume_device(root)
        if device is None:
            return {"volume": "still mounted"} if on_own_volume(root) else {"volume": "detached"}
        subprocess.run(
            ["/usr/bin/hdiutil", "detach", "-quiet", *extra, device], capture_output=True, timeout=60, check=False
        )
        if not on_own_volume(root):
            return {"volume": "detached"}
        time.sleep(1)
    return {"volume": "still mounted"}
