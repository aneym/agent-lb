from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pwd
import re
import shutil
import stat
import sys
import time
from pathlib import Path

REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)


LB_HOME = REAL_HOME / ".agent-lb"


SANDBOXES = LB_HOME / "sandboxes"


VOLUME_SIZE = "2g"


LIVE_RUNTIME = LB_HOME / "runtime" / "agent-lb"


LIVE_VENV = LIVE_RUNTIME / ".venv"


LIVE_PLIST = REAL_HOME / "Library" / "LaunchAgents" / "com.aneyman.agent-lb.plist"


LIVE_LABEL = "com.aneyman.agent-lb"


LIVE_FRONT_LABEL = "com.aneyman.agent-lb-front"


LIVE_FRONT_STATE = LB_HOME / "state" / "front.json"


LIVE_FRONT_PORT = 2455


LIVE_PRIMARY_PORT = 2457


LIVE_PEER_URL = "http://127.0.0.1:2455"


LIVE_OLD_KEY_DIR = REAL_HOME / ".codex-lb"


MANAGED_ROUTING = LB_HOME / "managed" / "coding-agents"


LABEL_PREFIX = "com.agent-lb.drill."


FORBIDDEN_PORTS = frozenset({1455, 2455, 2457, 2459})


MIN_PORT = 2470


PICK_RANGE = range(2470, 2600)


RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


TOKEN_ENV = "AGENT_LB_FEDERATION_TOKEN"


EDGES = {"anthropic": "https://api.anthropic.com", "openai": "https://chatgpt.com/backend-api"}


GATE_ALLOWED = frozenset({("GET", "/api/federation/mirror"), ("GET", "/api/federation/status")})


VENDOR_OF_PROVIDER = {"anthropic": "anthropic", "openai": "openai"}


USABLE_EXPIRY_S = 1800


DRAIN_S = 120


HOLD_MAX_S = 120


CUSTODY_FILES = (
    "data/store.db",
    "data/store.db-wal",
    "data/store.db-shm",
    "data/store.db-journal",
    "data/encryption.key",
)


ENV_MARKER = "LB_SANDBOX_ROOT="


EXIT_OK, EXIT_ASSERT, EXIT_REFUSED, EXIT_UNHEALTHY = 0, 1, 2, 3


DISABLED_SCHEDULERS = (
    "METRICS",
    "USAGE_REFRESH",
    "MODEL_REGISTRY",
    "ACCOUNT_PULSE",
    "RESET_CREDIT_AUTO_REDEEM",
    "RESET_CREDIT_EXPIRY_REDEEM",
    "ACCOUNTS_CACHE_WARMER",
    "STICKY_SESSION_CLEANUP",
    "QUOTA_PLANNER_SCHEDULER",
    "HTTP_RESPONSES_SESSION_BRIDGE",
    "AUTH_GUARDIAN",
)


class Refused(Exception):
    pass


class Unhealthy(Exception):
    pass


RESERVED_RUN_IDS = frozenset(
    {"state", "data", "bin", "runtime", "logs", "sandboxes", "managed", "backups", "shims", "tls", "team", "seats"}
)


def check_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
        raise Refused(f"run id {run_id!r} must match {RUN_ID_RE.pattern}")
    if run_id.endswith("-aux"):
        raise Refused(f"run id {run_id!r} must not end in -aux")
    if run_id in RESERVED_RUN_IDS or os.path.lexists(LB_HOME / run_id):
        raise Refused(f"run id {run_id!r} names a live agent-lb directory")
    return run_id


def check_label(label: str) -> str:
    suffix = label.removeprefix(LABEL_PREFIX)
    if not label.startswith(LABEL_PREFIX) or not suffix or not suffix.replace("-", "").isalnum():
        raise Refused(f"label {label!r} is not a {LABEL_PREFIX}* drill label")
    return label


def check_ports(ports: list[int]) -> list[int]:
    for port in ports:
        if isinstance(port, bool) or not isinstance(port, int) or port < MIN_PORT or port > 2599:
            raise Refused(f"port {port!r} is outside {MIN_PORT}-2599")
        if port in FORBIDDEN_PORTS:
            raise Refused(f"port {port} belongs to the live agent-lb")
    if len(set(ports)) != len(ports):
        raise Refused("sandbox ports repeat")
    return ports


def lexical(path: Path) -> Path:
    return Path(os.path.normpath(str(path)))


def _refuse_linked_dirs(what: str, path: Path) -> os.stat_result:
    if not path.is_absolute():
        raise Refused(f"{what} {path} is not absolute")
    info = None
    for i in range(1, len(path.parts) + 1):
        prefix = Path(*path.parts[:i])
        try:
            info = prefix.lstat()
        except OSError as exc:
            raise Refused(f"{what} {path}: {prefix} is missing ({exc.__class__.__name__})") from None
        if not stat.S_ISDIR(info.st_mode):
            raise Refused(f"{what} {path}: {prefix} is a link or not a directory")
    assert info is not None
    return info


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


def interpreter_for(root: Path) -> Path:
    return lexical(Path(root)).parent.parent / "runtime" / "agent-lb" / ".venv" / "bin" / "python"


NATIVE_MAGIC = (
    (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf")
    if sys.platform == "darwin"
    else (b"\x7fELF",)
)


BASE_INTERPRETER_NAME = re.compile(r"^python(3(\.[0-9]+)?)?$")


def _trusted_file(what: str, path: Path, info: os.stat_result) -> None:
    if info.st_uid not in (os.getuid(), 0) or info.st_mode & 0o022:
        raise Refused(f"{what} {path} is one another user could write")


def venv_base_dir(python: Path) -> Path:
    cfg = python.parent.parent / "pyvenv.cfg"
    try:
        fd = os.open(cfg, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise Refused(f"interpreter {python} has no venv config {cfg}") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise Refused(f"venv config {cfg} is not a single-link regular file")
        _trusted_file("venv config", cfg, info)
        text = os.read(fd, 1 << 16).decode(errors="replace")
    finally:
        os.close(fd)
    home = ""
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip().lower() == "home":
            home = value.strip()
    if not home.startswith("/"):
        raise Refused(f"venv config {cfg} names no absolute home")
    base = Path(os.path.realpath(home))
    try:
        _trusted_file("venv home", base, os.stat(base))
    except OSError:
        raise Refused(f"venv home {home} is missing") from None
    return base


def check_interpreter(python: Path, root: Path) -> None:
    python = lexical(python)
    sandboxes = lexical(Path(root)).parent
    if python != interpreter_for(root) or sandboxes in python.parents:
        raise Refused(f"interpreter {python} is not the installed runtime's venv python")
    _refuse_linked_dirs("interpreter", python.parent)
    base = venv_base_dir(python)
    try:
        target = Path(os.path.realpath(python))
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise Refused(f"interpreter {python} is missing") from None
    try:
        info = os.fstat(fd)
        head = os.read(fd, 4) if stat.S_ISREG(info.st_mode) else b""
    finally:
        os.close(fd)
    if target == sandboxes or sandboxes in target.parents:
        raise Refused(f"interpreter {python} leads into a sandbox root")
    if target.parent != base or not BASE_INTERPRETER_NAME.match(target.name):
        raise Refused(f"interpreter {python} does not lead to the base interpreter its venv config names")
    if not stat.S_ISREG(info.st_mode) or not info.st_mode & stat.S_IXUSR or head not in NATIVE_MAGIC:
        raise Refused(f"interpreter {python} does not lead to a native executable (a script or shim?)")
    _trusted_file("interpreter", target, info)


def bootstrap_script() -> Path:
    return Path(__file__).resolve().with_name("lb-sandbox")


def check_bootstrap(script: Path, root: Path) -> None:
    script = lexical(script)
    sandboxes = lexical(Path(root)).parent
    if script == sandboxes or sandboxes in script.parents:
        raise Refused(f"bootstrap {script} is inside the sandboxes dir")
    _refuse_linked_dirs("bootstrap", script.parent)
    try:
        info = os.lstat(script)
    except OSError:
        raise Refused(f"bootstrap {script} is missing") from None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise Refused(f"bootstrap {script} is not a single-link regular file")
    _trusted_file("bootstrap", script, info)


def check_launchctl() -> None:
    found = shutil.which("launchctl")
    if found != "/bin/launchctl":
        raise Refused(f"launchctl resolves to {found}, not /bin/launchctl")


def labels_for(run_id: str) -> tuple[str, str]:
    check_run_id(run_id)
    label = check_label(f"{LABEL_PREFIX}sbx-{run_id}")
    return label, check_label(f"{label}-aux")


def root_for(run_id: str) -> Path:
    return SANDBOXES / check_run_id(run_id)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class Confinement(OSError):
    pass


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
