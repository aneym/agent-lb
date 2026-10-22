from __future__ import annotations

import base64
import binascii
import datetime as dt
import errno
import hashlib
import os
import re
import sqlite3
import stat
import time
from pathlib import Path

from scripts.lb_sandbox_filesystem import (
    open_dir,
    open_root,
    run_confined,
)
from scripts.lb_sandbox_guards import (
    CUSTODY_FILES,
    LB_HOME,
    LIVE_OLD_KEY_DIR,
    LIVE_PLIST,
    SANDBOXES,
    USABLE_EXPIRY_S,
    VENDOR_OF_PROVIDER,
    Confinement,
    Private,
    Refused,
    Unhealthy,
    fd_path,
    identity,
    sandboxes_fd,
)
from scripts.lb_sandbox_system import (
    check_data_custody,
    make_fernet,
    on_run_volume,
    open_data_dir,
    store_rows,
)


def read_accounts(root: Path, *, with_tokens: bool = False, strict: bool = False) -> list[dict]:

    try:
        check_data_custody(root)
        data_fd = open_data_dir(root)
    except FileNotFoundError:
        data_fd = None
    except (Refused, OSError):
        if strict:
            raise Refused("sandbox data dir is linked, shared or not the root's; its tokens cannot be read") from None
        return []
    has_store = False
    if data_fd is not None:
        try:
            has_store = stat.S_ISREG(os.stat("store.db", dir_fd=data_fd, follow_symlinks=False).st_mode)
        except OSError:
            pass
        finally:
            os.close(data_fd)
    if not has_store:
        if strict:
            raise Refused("sandbox store missing; its account tokens cannot be scanned for")
        return []
    try:
        rows = run_confined(root, lambda: store_rows(root), reads=True, timeout_s=60)
    except (Refused, Unhealthy, sqlite3.Error):
        if strict:
            raise
        return []
    rows = [tuple(base64.b64decode(v["b64"]) if isinstance(v, dict) else v for v in row) for row in rows]
    fernet = make_fernet(root) if rows else None
    now = time.time()
    accounts = []

    def decrypt(blob) -> str:
        try:
            return fernet.decrypt(bytes(blob)).decode() if fernet and blob else ""
        except Exception:
            if strict:
                raise
            return ""

    for account_id, provider, status, expires_at, access_enc, id_enc, reset_at, blocked_at, refresh_enc in rows:
        token = decrypt(access_enc)
        expires_in = None
        if expires_at:
            try:
                stamp = dt.datetime.fromisoformat(str(expires_at)).replace(tzinfo=dt.timezone.utc)
                expires_in = int(stamp.timestamp() - now)
            except ValueError:
                expires_in = None
        cooling = status in ("rate_limited", "quota_exceeded") or bool(
            blocked_at and reset_at and float(reset_at) > now
        )
        entry = {
            "id": account_id,
            "vendor": VENDOR_OF_PROVIDER.get(str(provider), str(provider)),
            "status": status,
            "expires_in_s": expires_in,
            "auth_hash12": hashlib.sha256(f"Bearer {token}".encode()).hexdigest()[:12] if token else None,
            "in_cooldown": cooling,
            "cooldown_until": int(reset_at) if cooling and reset_at else None,
            "usable": status == "active" and expires_in is not None and expires_in > USABLE_EXPIRY_S,
        }
        if with_tokens:
            entry["_secrets"] = [secret for secret in (token, decrypt(id_enc), decrypt(refresh_enc)) if secret]

            entry["_ciphertexts"] = [
                bytes(blob).decode("ascii", "replace") for blob in (access_enc, id_enc, refresh_enc) if blob
            ]
        accounts.append(entry)
    return sorted(accounts, key=lambda a: (a["vendor"], a["id"]))


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


def secret_patterns(secret: str) -> list[bytes]:

    raw = secret.encode()
    patterns = {raw}
    for encode in (base64.b64encode, base64.urlsafe_b64encode):
        for shift, skip in ((0, 0), (1, 2), (2, 3)):
            encoded = encode(b"\0" * shift + raw).rstrip(b"=")

            whole = ((shift + len(raw)) * 8) // 6

            if encoded[skip:whole]:
                patterns.add(encoded[skip:whole])
    return sorted(p for p in patterns if p)


FERNET_FRAGMENT = re.compile(rb"(?=(gAAAAA[A-Za-z0-9_-]{50}))")


FERNET_FRAGMENT_CHARS = 56


B64_RUN = re.compile(rb"[A-Za-z0-9+/_\-\r\n]{75,}")


B64_FRAGMENT_SPAN = 96


B64_STANDARD = bytes.maketrans(b"-_", b"+/")


B64_CHAR = rb"[A-Za-z0-9+/=_-]"


WRAP_BREAK = re.compile(rb"(?<=" + B64_CHAR + rb")[ \t]*+\r?\n[ \t]*+(?=" + B64_CHAR + rb")")


WRAP_MIN_LINE = 16


WRAP_MAX_SEPARATOR = 8


def wrapped_span(length: int) -> int:

    return length + (length // WRAP_MIN_LINE + 1) * WRAP_MAX_SEPARATOR


def base64_decodings(buf: bytes):

    for match in B64_RUN.finditer(buf):
        text = match.group(0).translate(B64_STANDARD, b"\r\n")
        for offset in range(4):
            piece = text[offset:]
            piece = piece[: len(piece) - len(piece) % 4]
            if len(piece) >= 4:
                yield binascii.a2b_base64(piece)


def plausible_plaintext(block: bytes) -> bool:

    pad = block[-1]
    body = block[:-pad] if 1 <= pad <= 16 and block.endswith(bytes([pad]) * pad) else block
    return bool(body) and all(0x20 <= b < 0x7F for b in body)


class Detector:
    def __init__(self, secrets: list[str], keys: list[str] | None = None) -> None:
        self.patterns = sorted({p for s in secrets if s for p in secret_patterns(s)})
        self.decryptors = []
        for key in keys or []:
            if not key:
                continue
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

            raw = base64.urlsafe_b64decode(key.encode() if isinstance(key, str) else key)
            self.decryptors.append(Cipher(algorithms.AES(raw[16:]), modes.ECB()).decryptor())

        lengths = [wrapped_span(len(p)) for p in self.patterns]
        if self.decryptors:
            lengths.append(wrapped_span(B64_FRAGMENT_SPAN))
        self.overlap = max(lengths, default=1) - 1

    def __bool__(self) -> bool:
        return bool(self.patterns)

    def opens(self, fragment: bytes) -> bool:
        raw = base64.urlsafe_b64decode(fragment[:FERNET_FRAGMENT_CHARS])
        iv, first = raw[9:25], raw[25:41]
        return any(
            plausible_plaintext(bytes(a ^ b for a, b in zip(dec.update(first), iv, strict=True)))
            for dec in self.decryptors
        )

    def keyed(self, buf: bytes) -> bool:
        return b"gAAAAA" in buf and any(self.opens(m.group(1)) for m in FERNET_FRAGMENT.finditer(buf))

    @staticmethod
    def unwrapped(buf: bytes) -> bytes | None:

        if b"\n" not in buf:
            return None
        joined = WRAP_BREAK.sub(b"", buf)
        return joined if len(joined) != len(buf) else None

    def hit(self, buf: bytes) -> bool:
        if any(p in buf for p in self.patterns):
            return True
        joined = self.unwrapped(buf)

        if joined is not None and any(p in joined for p in self.patterns):
            return True
        if not self.decryptors:
            return False

        for view in (buf,) if joined is None else (buf, joined):
            if self.keyed(view) or any(self.keyed(decoded) for decoded in base64_decodings(view)):
                return True
        return False


def scan_stream(read, detector: Detector, write=None, chunk: int = 4 << 20) -> tuple[bool, int]:

    tail, total = b"", 0
    while True:
        block = read(chunk)
        if not block:
            return False, total
        total += len(block)
        buf = tail + block
        if detector.hit(buf):
            return True, total
        if write is not None:
            write(block)
        tail = buf[-detector.overlap :] if detector.overlap > 0 else b""


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


def under(path: str, base: str) -> bool:
    return path == base or path.startswith(base + os.sep)


def root_text(root: Path) -> str:

    try:
        parent = fd_path(sandboxes_fd(Path(root).parent))
    except OSError:
        parent = os.path.realpath(Path(root).parent)
    return os.path.join(parent, Path(root).name)


def live_trees() -> list[str]:

    return [os.path.abspath(LB_HOME), os.path.abspath(LIVE_OLD_KEY_DIR)]


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


def open_top(top: Path, root: Path | None) -> tuple[int, str]:

    text = os.path.abspath(top)
    parent, name = os.path.split(text)
    resolved = os.path.join(system_links_resolved(parent), name) if name else system_links_resolved(text)
    if root is not None:
        base = root_text(root)
        for candidate in (text, resolved):
            if under(candidate, base):
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
