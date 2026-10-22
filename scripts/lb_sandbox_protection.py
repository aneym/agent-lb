from __future__ import annotations
import argparse
import json
import os
import signal
import stat
import sys
from pathlib import Path
from lb_sandbox_boot_environment import (
    enter_data_dir,
    read_token_fd,
)
from lb_sandbox_filesystem import (
    read_root_json,
)
from lb_sandbox_paths import (
    BASE_INTERPRETER_NAME,
    LB_HOME,
    LIVE_KEY_DIR_LEGACY,
    LIVE_PLIST,
    LIVE_RUNTIME,
    NATIVE_MAGIC,
    REAL_HOME,
    Refused,
    TOKEN_ENV,
    Unhealthy,
    bootstrap_script,
    check_ports,
)
from lb_sandbox_private_storage import (
    check_root,
    fd_path,
    root_entry,
    sandboxes_fd,
)
from lb_sandbox_volumes import (
    check_own_volume,
)


def _lexical(path: Path) -> Path:
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


def interpreter_for(root: Path) -> Path:
    return _lexical(Path(root)).parent.parent / "runtime" / "agent-lb" / ".venv" / "bin" / "python"


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
    python = _lexical(python)
    sandboxes = _lexical(Path(root)).parent
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


def check_bootstrap(script: Path, root: Path) -> None:
    script = _lexical(script)
    sandboxes = _lexical(Path(root)).parent
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
        custody = f"(subpath {q(LB_HOME)}) (subpath {q(LIVE_KEY_DIR_LEGACY)}) (literal {q(LIVE_PLIST)})"
        rules += [

            f"(deny file-read-data {custody})",

            f"(allow file-read-data (subpath {q(runtime)}))",
            f"(deny file-read-data {private})",
        ]


    if os.path.realpath(Path(root).parent) != str(_lexical(Path(root).parent)):
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

    if not pid or sys.platform != "darwin":
        return None
    try:
        return _libsystem().sandbox_check(int(pid), None, 0) == 1
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
            out = {"ok": action()}
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
    kind = {"Refused": Refused, "Unhealthy": Unhealthy}.get(out.get("error"), Unhealthy)
    raise kind(f"confined step failed: {out.get('error', 'no result')}: {out.get('message', '')}")


def boot_app(root: Path, argv: list[str]) -> None:


    sandboxes = _lexical(Path(root)).parent
    check_bootstrap(bootstrap_script(), Path(root))
    root = check_root(root, sandboxes=sandboxes)


    check_own_volume(root)
    confine(root, reads=True)
    parser = argparse.ArgumentParser(prog="lb-sandbox _boot")
    parser.add_argument("--token-fd", type=int, required=True)
    parser.add_argument("--data-id", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    opts = parser.parse_args(argv)
    if TOKEN_ENV in os.environ:
        raise Refused("the federation token is in the exec environment; refusing to start")
    if os.environ.get("LB_SANDBOX_ROOT") != str(root):
        raise Refused("LB_SANDBOX_ROOT does not match the sandbox root")
    meta = read_root_json(root, "sandbox.json")
    if opts.host != "127.0.0.1" or opts.port not in {meta.get("ports", {}).get(k) for k in ("primary", "standby")}:
        raise Refused("_boot serves only this sandbox's primary or standby port on 127.0.0.1")
    check_ports([opts.port])
    enter_data_dir(root, opts.data_id)
    token = read_token_fd(opts.token_fd)
    if str(root / "runtime") not in sys.path:
        sys.path.insert(0, str(root / "runtime"))
    os.environ["PORT"] = str(opts.port)
    os.environ[TOKEN_ENV] = token
    try:
        from app.core.config.settings import get_settings

        loaded = get_settings().federation_token == token
    finally:
        os.environ.pop(TOKEN_ENV, None)
        del token
    if not loaded:
        raise Unhealthy("the app settings did not take the federation token")
    from app.cli import main as run_app

    run_app(["--host", opts.host, "--port", str(opts.port)])
