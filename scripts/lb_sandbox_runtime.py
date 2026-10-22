from __future__ import annotations

import argparse
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

from scripts.lb_sandbox_filesystem import (
    check_own_volume,
    confined,
    open_in_root,
    read_json,
    read_root_json,
    read_root_text,
    remove_private,
    remove_recorded,
    root_stage,
    write_in_root,
    write_json,
)
from scripts.lb_sandbox_guards import (
    DRAIN_S,
    EXIT_UNHEALTHY,
    LIVE_FRONT_LABEL,
    LIVE_FRONT_PORT,
    LIVE_FRONT_STATE,
    LIVE_LABEL,
    LIVE_PRIMARY_PORT,
    LIVE_RUNTIME,
    LIVE_VENV,
    MANAGED_ROUTING,
    REAL_HOME,
    Private,
    Refused,
    Unhealthy,
    bootstrap_script,
    check_bootstrap,
    check_interpreter,
    check_label,
    check_ports,
    check_root,
    interpreter_for,
    labels_for,
)
from scripts.lb_sandbox_scan import sandbox_env
from scripts.lb_sandbox_system import (
    check_data_custody,
    check_data_custody_fd,
    label_loaded,
    launchd_pid,
    listener_pids,
    live_federation_token,
    names_root,
    open_data_dir,
    own_job_print,
    run,
)


def plist_bodies(
    root: Path, run_id: str, ports: dict[str, int], transport: str, script: Path | None = None
) -> tuple[dict, dict]:

    label, aux_label = labels_for(run_id)
    python = str(interpreter_for(root))
    script_path = str(script or bootstrap_script())
    primary = {
        "Label": label,
        "ProgramArguments": [
            python,
            "-I",
            script_path,
            "_serve",
            str(root),
            "--host",
            "127.0.0.1",
            "--port",
            str(ports["primary"]),
        ],
        "WorkingDirectory": str(root / "runtime"),
        "EnvironmentVariables": sandbox_env(root, ports, run_id, transport),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 2,
        "ExitTimeOut": DRAIN_S + 30,
    }
    aux = {
        "Label": aux_label,
        "ProgramArguments": [python, "-I", script_path, "_aux", str(root)],
        "WorkingDirectory": str(root),
        "EnvironmentVariables": {
            "HOME": str(root / "home"),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "LB_SANDBOX_ROOT": str(root),
        },
        "RunAtLoad": True,
        "KeepAlive": False,
        "ExitTimeOut": 20,
    }

    return primary, aux


def write_plists(
    root: Path, run_id: str, ports: dict[str, int], transport: str, script: Path | None = None
) -> tuple[Path, Path]:

    paths = plist_paths(root, run_id)
    for path, body in zip(paths, plist_bodies(root, run_id, ports, transport, script), strict=True):
        write_in_root(root, path.relative_to(root), plistlib.dumps(body))
    return paths


def find_node() -> str:
    for candidate in (
        run(["volta", "which", "node"]).stdout.strip() if shutil.which("volta") else "",
        shutil.which("node") or "",
        "/opt/homebrew/bin/node",
    ):
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    raise Unhealthy("node not found for the sandbox front")


def live_identity() -> dict:
    preferred = read_json(LIVE_FRONT_STATE).get("preferred")
    return {
        "primary_pid": launchd_pid(LIVE_LABEL),
        "front_pid": launchd_pid(LIVE_FRONT_LABEL),
        "front_port": LIVE_FRONT_PORT,
        "front_listener_pids": listener_pids(LIVE_FRONT_PORT),
        "primary_listener_pids": listener_pids(LIVE_PRIMARY_PORT),
        "preferred_port": preferred,
    }


def sm_job_submit(body: dict) -> bool:

    import ctypes

    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    sm = ctypes.CDLL("/System/Library/Frameworks/ServiceManagement.framework/ServiceManagement")
    cf.CFDataCreate.restype = ctypes.c_void_p
    cf.CFDataCreate.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long]
    cf.CFPropertyListCreateWithData.restype = ctypes.c_void_p
    cf.CFPropertyListCreateWithData.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    sm.SMJobSubmit.restype = ctypes.c_bool
    sm.SMJobSubmit.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    domain = ctypes.c_void_p.in_dll(sm, "kSMDomainUserLaunchd")
    data = plistlib.dumps(body, fmt=plistlib.FMT_BINARY)
    cfdata = cf.CFDataCreate(None, data, len(data))
    if not cfdata:
        return False
    job = cf.CFPropertyListCreateWithData(None, cfdata, 0, None, None)
    try:
        return bool(job) and bool(sm.SMJobSubmit(domain, job, None, None))
    finally:
        if job:
            cf.CFRelease(job)
        cf.CFRelease(cfdata)


def own_job_loaded(label: str) -> bool:

    return run(["/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"]).returncode == 0


def submit_job(body: dict, deadline_s: float = 20) -> None:

    label = check_label(str(body.get("Label", "")))
    if label_loaded(label) or own_job_loaded(label):
        raise Refused(f"{label} is already loaded; start never replaces a job")
    if not sm_job_submit(body):
        raise Unhealthy(f"launchd refused the job {label}")
    deadline = time.monotonic() + deadline_s
    while not own_job_loaded(label):
        if time.monotonic() > deadline:
            run(["/bin/launchctl", "remove", label])
            raise Unhealthy(f"the job {label} is not in gui/{os.getuid()} (a session without a GUI domain?)")
        time.sleep(0.25)


def label_names_root(label: str, root: Path, run_id: str) -> bool:

    out = own_job_print(label, run_id)
    return any(names_root(line.strip(), root) for line in out.splitlines())


def bootout(label: str, deadline_s: float = 30) -> bool:
    check_label(label)
    target = f"gui/{os.getuid()}/{label}"
    run(["/bin/launchctl", "bootout", target], timeout=deadline_s + 10)
    deadline = time.monotonic() + deadline_s
    while label_loaded(label):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.5)
    return True


def usable_counts(accounts: list[dict]) -> dict[str, int]:
    counts = {"anthropic": 0, "openai": 0}
    for account in accounts:
        if account["usable"] and account["vendor"] in counts:
            counts[account["vendor"]] += 1
    return counts


def log_failure_classes(root: Path) -> dict:

    text = read_root_text(root, "logs/primary.log", 200_000)
    if not text:
        return {}
    return {
        "exception_classes": sorted(set(re.findall(r"^([A-Za-z0-9_.]+(?:Error|Exception)):", text, re.MULTILINE))),
        "frames": re.findall(r'File "[^\n"]*/(app/[^\n"]+)", line ([0-9]+), in ([A-Za-z0-9_]+)', text)[-8:],
    }


def plist_paths(root: Path, run_id: str) -> tuple[Path, Path]:
    label, aux_label = labels_for(run_id)
    return root / "launchd" / f"{label}.plist", root / "launchd" / f"{aux_label}.plist"


RUNTIME_PARTS = ("app", "config", "scripts")


DEFAULT_REPO = Path(os.environ.get("AGENT_LB_REPO") or "/Volumes/StudioExt/repos/agent-lb")


GIT_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@{}^~-]{0,199}$")


def ref_tree(repo: Path, ref: str) -> tuple[str, bytes]:

    if not GIT_REF_RE.match(ref or "") or ".." in ref:
        raise Refused(f"--from-ref {ref!r} is not a plain git ref")
    git = shutil.which("git") or "/usr/bin/git"
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin", "HOME": str(REAL_HOME), "LANG": "C"}
    base = [git, "-C", str(repo), "-c", "core.fsmonitor=false"]
    found = subprocess.run(
        [*base, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}"],
        capture_output=True,
        timeout=60,
        check=False,
        env=env,
    )
    sha = found.stdout.decode(errors="replace").strip()
    if found.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise Unhealthy(f"--from-ref {ref!r} does not name a commit in {repo}")
    archive = subprocess.run(
        [*base, "archive", "--format=tar", sha, "--", *RUNTIME_PARTS],
        capture_output=True,
        timeout=300,
        check=False,
        env=env,
    )
    if archive.returncode != 0 or not archive.stdout:
        raise Unhealthy(f"git archive of {sha[:12]} failed (exit {archive.returncode})")
    return sha, archive.stdout


def extract_tree(tree: bytes, runtime: Path) -> None:

    import io
    import tarfile

    with tarfile.open(fileobj=io.BytesIO(tree), mode="r:") as archive:
        members = [m for m in archive.getmembers() if m.isfile() or m.isdir()]
        archive.extractall(runtime, members=members, filter="data")


def populate_root(
    root: Path,
    run_id: str,
    ports: dict[str, int],
    meta: dict,
    transport: str,
    source: str,
    tree: bytes | None = None,
) -> None:

    for sub in ("data", "home", "logs", "state", "clients", "runtime"):
        os.mkdir(root / sub, 0o700)
    write_json(root, "sandbox.json", meta)
    runtime = root / "runtime"
    if tree is not None:
        extract_tree(tree, runtime)
    else:
        for part in RUNTIME_PARTS:
            if (LIVE_RUNTIME / part).is_dir():
                shutil.copytree(
                    LIVE_RUNTIME / part, runtime / part, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
                )
    os.symlink(LIVE_VENV, runtime / ".venv")
    managed = Path("home/.agent-lb/managed/coding-agents")
    for name in ("routing-table.json", "decider.json"):
        if (MANAGED_ROUTING / name).is_file():
            write_in_root(root, managed / name, (MANAGED_ROUTING / name).read_bytes())
    primary_plist, _ = write_plists(root, run_id, ports, transport, Path(source))
    write_json(
        root,
        "lb-restart.json",
        {
            "root": str(root),
            "label": meta["label"],
            "plist": str(primary_plist),
            "runtime": str(runtime),
            "state_dir": str(root / "state"),
            "front_port": ports["front"],
            "primary_port": ports["primary"],
            "standby_port": ports["standby"],
        },
    )
    write_in_root(root, "state/front-preferred-port", f"{ports['primary']}\n".encode(), 0o644)


CREATED: dict[str, Private] = {}


def cleanup_unpublished(priv: Private) -> dict:

    out: dict = {}
    if "root" in priv.ids:
        out["stage_removed"] = remove_recorded(priv.fd, root_stage(priv.ids), priv.ids["root"], "dir")
    out["private_removed"] = remove_private(priv)
    return out


def serve_exec_args(root: Path, argv: list[str]) -> tuple[str, list[str], dict[str, str], str]:

    root = check_root(root)
    meta = read_root_json(root, "sandbox.json")
    if not meta or meta.get("run_id") != root.name:
        raise Refused(f"{root} has no sandbox.json stamped with its run id")
    parser = argparse.ArgumentParser(prog="lb-sandbox _serve")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    opts, extra = parser.parse_known_args(argv)
    if extra:
        raise Refused(f"_serve takes only --host and --port, not {' '.join(extra)}")
    if opts.host != "127.0.0.1":
        raise Refused("the sandbox binds 127.0.0.1 only")
    expected_ports = {"front", "primary", "standby", "gate", "edge_anthropic", "edge_openai"}
    if not isinstance(meta.get("ports"), dict) or set(meta["ports"]) != expected_ports:
        raise Refused("sandbox metadata must contain exactly the six listener ports")
    check_ports(list(meta["ports"].values()))

    for relative in (
        "data",
        "data/store.db",
        "data/encryption.key",
        "data/federation-push.json",
        "data/conversation-archive",
    ):
        path = root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise Refused(f"sandbox data path {relative} is redirected outside the root")

    check_data_custody(root)
    try:
        key_fd = open_in_root(root, "data/encryption.key", os.O_RDONLY)
    except OSError as exc:
        raise Refused(f"sandbox store key is missing or redirected ({exc.__class__.__name__})") from None
    os.close(key_fd)

    try:
        data_fd = open_data_dir(root)
    except OSError as exc:
        raise Refused(f"sandbox data dir is missing or redirected ({exc.__class__.__name__})") from None
    data = os.fstat(data_fd)
    os.close(data_fd)
    allowed = {meta["ports"]["primary"], meta["ports"]["standby"]}
    check_ports([opts.port])
    if opts.port not in allowed:
        raise Refused(f"port {opts.port} is not this sandbox's primary or standby port")
    if os.environ.get("LB_SANDBOX_ROOT") != str(root):
        raise Refused("LB_SANDBOX_ROOT does not match the sandbox root")

    env = sandbox_env(root, meta["ports"], root.name, meta.get("transport", "http"))
    env.update({k: os.environ[k] for k in ("USER", "LOGNAME", "TMPDIR", "LANG") if k in os.environ})
    token = live_federation_token()
    if not token:
        raise Unhealthy("live plist has no federation token")
    python = str(interpreter_for(root))
    check_interpreter(Path(python), root)
    script = bootstrap_script()
    check_bootstrap(script, root)

    args = [
        python,
        "-I",
        str(script),
        "_boot",
        str(root),
        "--data-id",
        f"{data.st_dev}:{data.st_ino}",
        "--host",
        "127.0.0.1",
        "--port",
        str(opts.port),
    ]
    return python, args, env, token


def token_pipe(token: str) -> int:

    read_fd, write_fd = os.pipe()
    try:
        data = token.encode()
        while data:
            data = data[os.write(write_fd, data) :]
    finally:
        os.close(write_fd)
    os.set_inheritable(read_fd, True)
    return read_fd


def log_to_root(root: Path, rel: str) -> None:

    try:
        out, null = os.fstat(1), os.stat(os.devnull)
        if (out.st_dev, out.st_ino) != (null.st_dev, null.st_ino):
            return
    except OSError:
        pass
    fd = confined(lambda: open_in_root(root, rel, os.O_WRONLY | os.O_APPEND | os.O_CREAT, create_dirs=True))
    sys.stdout.flush()
    sys.stderr.flush()
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.close(fd)


def cmd_serve(root: str, argv: list[str]) -> int:
    root_path = check_root(Path(root))
    log_to_root(root_path, "logs/primary.log")
    check_own_volume(root_path)
    python, args, env, token = serve_exec_args(root_path, argv)
    read_fd = token_pipe(token)
    del token
    os.chdir(root_path / "runtime")
    os.execve(python, [*args[:5], "--token-fd", str(read_fd), *args[5:]], env)
    return EXIT_UNHEALTHY


def read_token_fd(fd: int) -> str:

    try:
        if not stat.S_ISFIFO(os.fstat(fd).st_mode):
            raise Refused(f"--token-fd {fd} is not a pipe")
        chunks = []
        while chunk := os.read(fd, 65536):
            chunks.append(chunk)
    finally:
        os.close(fd)
    token = b"".join(chunks).decode().strip()
    if not token:
        raise Unhealthy("the token pipe was empty")
    return token


RELATIVE_DATA_ENV = {
    "AGENT_LB_DATA_DIR": ".",
    "AGENT_LB_DATABASE_URL": "sqlite+aiosqlite:///store.db",
    "AGENT_LB_ENCRYPTION_KEY_FILE": "encryption.key",
    "AGENT_LB_FEDERATION_PUSH_PATH": "federation-push.json",
    "AGENT_LB_CONVERSATION_ARCHIVE_DIR": "conversation-archive",
}


def enter_data_dir(root: Path, data_id: str) -> None:

    match = re.fullmatch(r"([0-9]+):([0-9]+)", data_id or "")
    if not match:
        raise Refused("--data-id must be <device>:<inode>")
    try:
        data_fd = open_data_dir(root, expect=(int(match.group(1)), int(match.group(2))))
    except FileNotFoundError:
        raise Refused("sandbox data dir is missing") from None
    except OSError as exc:
        raise Refused(f"sandbox data dir is a link or not the root's ({exc.__class__.__name__})") from None
    try:
        check_data_custody_fd(data_fd)
        os.fchdir(data_fd)
    finally:
        os.close(data_fd)
    os.environ.update(RELATIVE_DATA_ENV)
