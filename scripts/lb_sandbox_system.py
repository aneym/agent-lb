from __future__ import annotations

import base64
import json
import os
import plistlib
import socket
import sqlite3
import stat
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from scripts.lb_sandbox_filesystem import (
    IMAGE_MAGIC,
    in_dir,
    on_own_volume,
    open_in_root,
    open_root,
    read_root_json,
    statfs_from,
    volume_device,
    write_in_root,
)
from scripts.lb_sandbox_guards import (
    BIRTH_SLACK_S,
    CUSTODY_FILES,
    DIR_FLAGS,
    FORBIDDEN_PORTS,
    IMAGE_NAME,
    LABEL_PREFIX,
    LIVE_PLIST,
    PICK_RANGE,
    RUN_ID_RE,
    SANDBOXES,
    TOKEN_ENV,
    VOLUME_SIZE,
    Confinement,
    Private,
    Refused,
    Unhealthy,
    born,
    check_root,
    fd_path,
    identity,
    labels_for,
    root_for,
    sandboxes_fd,
)


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
        preexec_fn=in_dir(priv.fd),
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
        preexec_fn=in_dir(priv.fd),
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
        if info.st_dev == os.fstat(priv.sb).st_dev or statfs_from(fd) != device:
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


def http(port: int, path: str, timeout: float = 3.0) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except Exception:
        return 0, b""


def port_free(port: int) -> bool:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def sandbox_roots() -> list[Path]:

    try:
        with os.scandir(sandboxes_fd()) as listing:
            entries = sorted(((e.name, e.is_dir(follow_symlinks=False)) for e in listing), key=lambda e: e[0])
    except OSError:
        return []
    return [SANDBOXES / name for name, is_dir in entries if RUN_ID_RE.match(name) and is_dir]


def ports_in_use_by_sandboxes() -> set[int]:
    used: set[int] = set()
    for root in sandbox_roots():
        ports = read_root_json(root, "sandbox.json").get("ports", {})
        used.update(int(p) for p in (ports.values() if isinstance(ports, dict) else []) if isinstance(p, int))
    return used


def pick_ports(count: int, taken: set[int]) -> list[int]:
    picked: list[int] = []
    for port in PICK_RANGE:
        if port in FORBIDDEN_PORTS or port in taken or port in picked:
            continue
        if port_free(port):
            picked.append(port)
            if len(picked) == count:
                return picked
    raise Unhealthy("no free sandbox ports in 2470-2599")


SANDBOX_LABEL_PREFIX = f"{LABEL_PREFIX}sbx-"


def refuse_foreign_launchctl_print(argv: list[str]) -> None:

    if len(argv) >= 2 and os.path.basename(str(argv[0])) == "launchctl" and argv[1] == "print":
        target = str(argv[2]) if len(argv) > 2 else ""
        label = target.rsplit("/", 1)[-1]
        if not (target.startswith(f"gui/{os.getuid()}/") and label.startswith(SANDBOX_LABEL_PREFIX)):
            raise Refused(f"launchctl print of {target!r} refused: only a sandbox's own job is printed")


def run(argv: list[str], timeout: float = 30) -> subprocess.CompletedProcess:

    refuse_foreign_launchctl_print(argv)
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


def launchctl_jobs() -> dict[str, int | None]:

    out = run(["/bin/launchctl", "list"]).stdout
    lines = out.splitlines()
    if not lines or lines[0].split() != ["PID", "Status", "Label"]:
        raise Unhealthy("launchctl list printed no job table")
    jobs: dict[str, int | None] = {}
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) == 3:
            jobs[parts[2]] = int(parts[0]) if parts[0].isdigit() else None
    return jobs


def launchd_pid(label: str) -> int | None:
    return launchctl_jobs().get(label)


def label_loaded(label: str) -> bool:
    return label in launchctl_jobs()


def own_job_print(label: str, run_id: str) -> str:

    if label not in labels_for(run_id):
        raise Refused(f"launchctl print of {label} refused: not a job of run {run_id}")
    return run(["/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"]).stdout


def listener_pids(port: int) -> list[int]:
    out = run(["/usr/sbin/lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"]).stdout.split()
    return sorted({int(pid) for pid in out if pid.isdigit()})


def listener_addresses(pids: list[int]) -> list[dict]:

    if not pids:
        return []
    out = run(["/usr/sbin/lsof", "-nP", "-a", "-p", ",".join(map(str, pids)), "-iTCP", "-sTCP:LISTEN"]).stdout
    found = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 9:
            found.append({"pid": int(parts[1]), "address": parts[8]})
    return found


def processes() -> list[tuple[int, int, str]]:
    out = run(["/bin/ps", "-axww", "-o", "pid=,ppid=,command="]).stdout
    rows = []
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
    return rows


def names_root(cmd: str, root: Path) -> bool:

    text = str(root)
    return any(token == text or token.startswith(text + "/") for token in cmd.split())


def names_label(cmd: str, run_id: str) -> bool:

    labels = set(labels_for(run_id))
    return any(token.rsplit("/", 1)[-1] in labels for token in cmd.split() if token.startswith(("gui/", LABEL_PREFIX)))


def names_run(cmd: str, run_id: str) -> bool:

    return names_label(cmd, run_id) or run_argument(cmd, run_id)


def run_argument(cmd: str, run_id: str) -> bool:

    tokens = cmd.split()
    return any(
        token == f"--run-id={run_id}" or (token == "--run-id" and tokens[i + 1 : i + 2] == [run_id])
        for i, token in enumerate(tokens)
    )


def owned_process(cmd: str, root: Path, run_id: str) -> bool:

    return names_root(cmd, root) or names_label(cmd, run_id)


def own_lineage(rows: list[tuple[int, int, str]]) -> set[int]:
    parents = {pid: ppid for pid, ppid, _ in rows}
    lineage, pid = set(), os.getpid()
    while pid and pid not in lineage:
        lineage.add(pid)
        pid = parents.get(pid, 0)
    return lineage


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def emit(data: dict, code: int) -> int:
    print(json.dumps(data, indent=1, sort_keys=True), flush=True)
    return code


def stamped_meta(root: Path, run_id: str) -> dict:

    meta = read_root_json(root, "sandbox.json")
    if meta.get("run_id") != run_id:
        return {}
    if "label" in meta and meta["label"] != labels_for(run_id)[0]:
        return {}
    return meta


def load_meta(run_id: str) -> tuple[Path, dict]:
    root = check_root(root_for(run_id))
    meta = stamped_meta(root, run_id)
    if not meta:
        raise Unhealthy(f"no sandbox {run_id} (no sandbox.json stamped with this run id under {root})")
    return root, meta


def live_federation_token(plist: Path | None = None) -> str | None:

    with (plist or LIVE_PLIST).open("rb") as fh:
        env = plistlib.load(fh).get("EnvironmentVariables", {})
    value = env.get(TOKEN_ENV)
    return str(value) if value else None


def make_fernet(root: Path):
    from cryptography.fernet import Fernet

    key = read_key(root)
    if not key:
        raise Refused("sandbox store key missing")
    return Fernet(key.encode())


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
