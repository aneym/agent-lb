from __future__ import annotations
import hashlib
import os
import signal
import time
from pathlib import Path
from lb_sandbox_account_reader import (
    load_key,
    load_secrets,
)
from lb_sandbox_credentials import (
    custody_paths,
    entry_of,
)
from lb_sandbox_detection import (
    Detector,
)
from lb_sandbox_filesystem import (
    open_dir,
    open_root,
    read_root_text,
)
from lb_sandbox_launchd import (
    bootout,
    label_names_root,
)
from lb_sandbox_log_export import (
    LeakFound,
    export_logs,
)
from lb_sandbox_ownership import (
    CREATED,
)
from lb_sandbox_paths import (
    DIR_FLAGS,
    Refused,
    labels_for,
    root_for,
)
from lb_sandbox_private_storage import (
    IMAGE_NAME,
    open_private,
    private_names,
    remove_private,
    remove_recorded,
    root_stage,
    sandboxes_fd,
    sandboxes_id,
)
from lb_sandbox_processes import (
    label_loaded,
    listener_pids,
    names_root,
    names_run,
    own_lineage,
    owned_process,
    pid_alive,
    processes,
    stamped_meta,
)
from lb_sandbox_traversal import (
    root_text,
    scan_paths,
)
from lb_sandbox_volumes import (
    detach_volume,
    image_exists,
    on_own_volume,
    on_run_volume,
)


def stop_pid(pid: int, bound: float = 15) -> None:
    for sig, wait in ((signal.SIGTERM, bound), (signal.SIGKILL, 5)):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + wait
        while pid_alive(pid) and time.monotonic() < deadline:
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass
            time.sleep(0.2)
        if not pid_alive(pid):
            return


def unlink_key(root: Path, ids: dict) -> dict:

    if entry_of(root) is None:
        return {"key_unlinked": None}
    key_id = ids.get("key")
    if not on_run_volume(root, ids):
        if key_id is None:
            return {"key_unlinked": "never created"}
        if on_own_volume(root):
            return {"key_unlinked": False, "key_error": "the root is a volume this run did not attach"}
        return {"key_unlinked": "in the image", "key_note": "the run's volume is not mounted; the image goes"}
    try:
        root_fd = open_root(root)
    except OSError as exc:
        return {"key_unlinked": False, "key_error": f"the root is a link ({exc.__class__.__name__})"}
    try:
        try:
            data_fd = os.open("data", DIR_FLAGS, dir_fd=root_fd)
        except FileNotFoundError:
            return {"key_unlinked": "absent"}
        except OSError as exc:
            return {"key_unlinked": False, "key_error": f"the data dir is a link ({exc.__class__.__name__})"}
    finally:
        os.close(root_fd)
    try:
        if os.fstat(data_fd).st_dev != ids["volume"]["st_dev"]:
            return {"key_unlinked": False, "key_error": "the data dir is not on the run's volume"}
        if key_id is None:
            try:
                os.stat("encryption.key", dir_fd=data_fd, follow_symlinks=False)
            except FileNotFoundError:
                return {"key_unlinked": "absent"}
            return {"key_unlinked": False, "key_error": "a key no start recorded; not unlinked"}
        removed = remove_recorded(data_fd, "encryption.key", key_id, "file")
    except OSError as exc:
        return {"key_unlinked": False, "key_error": exc.__class__.__name__}
    finally:
        os.close(data_fd)
    if removed is None:
        return {"key_unlinked": "absent"}
    return {"key_unlinked": True} if removed else {"key_unlinked": False, "key_error": "not the key start made"}


def has_dir(root: Path, rel: str) -> bool:

    try:
        os.close(open_dir(root, rel))
    except OSError:
        return False
    return True


def remove_root(root: Path, root_id: list[int] | None, priv: Private | None) -> bool:

    if priv is None or root_id is None:
        return entry_of(root) is None
    sb = sandboxes_fd(Path(root).parent)
    if on_own_volume(root):
        return False
    return remove_recorded(sb, Path(root).name, root_id, "dir", into=priv.fd) is not False


def command_ref(cmd: str) -> str:

    return hashlib.sha256(cmd.encode(errors="replace")).hexdigest()[:12]


def teardown(run_id: str, logs_to: Path | None, created: bool = False) -> dict:

    root = root_for(run_id)


    entry = entry_of(root)
    pin_error = "the sandboxes dir is a link or not private" if entry == "unknown" else None
    real_root = entry == "dir"
    meta = stamped_meta(root, run_id) if real_root else {}
    if meta and isinstance(meta.get("sandboxes_id"), list) and list(meta["sandboxes_id"]) != list(sandboxes_id()):
        meta, pin_error = {}, "the sandboxes dir is not the one this run was made in"
    owned = (bool(meta) or created) and not pin_error
    label, aux_label = labels_for(run_id)
    meta_ports = meta.get("ports") if isinstance(meta.get("ports"), dict) else {}
    ports = [int(p) for p in meta_ports.values() if isinstance(p, int) and not isinstance(p, bool)]
    secrets: list[str] = []
    keys: list[str] = []
    load_error = None
    if owned:
        try:
            secrets = load_secrets(root if real_root else None)
            key = load_key(root) if real_root else None
            keys = [key] if key else []
        except Exception as exc:
            secrets, keys = [], []
            load_error = exc.__class__.__name__
    booted = {}
    for name in (label, aux_label):
        if not label_loaded(name):
            booted[name] = "not loaded"
        elif label_names_root(name, root, run_id):
            booted[name] = bootout(name)
        else:
            booted[name] = "refused: the loaded job does not run this sandbox root"
    refused = None
    if owned:
        rows = processes()
        lineage = own_lineage(rows)
        standby = read_root_text(root, "state/lb-standby.pid", 64).strip() if real_root else ""
        pid = int(standby) if standby.isdigit() else 0
        if pid and any(p == pid and names_root(cmd, root) for p, _, cmd in rows):
            stop_pid(pid)

        for pid, _, cmd in processes():
            if owned_process(cmd, root, run_id) and pid not in lineage:
                stop_pid(pid, bound=5)
    elif pin_error:
        refused = f"{pin_error}: nothing was signalled, scanned or deleted"
    elif entry is not None:
        refused = "no sandbox.json stamped with this run id: nothing was signalled, scanned or deleted"
    logs: dict = {"copied": False}
    if owned and real_root:

        scanned = scan_paths([root], secrets, skip_exact=custody_paths(root), keys=keys, root=root)
        logs["scan_found"] = scanned["found"]
        logs["scan_complete"] = scanned["complete"]
        logs["files_scanned"] = scanned["files_scanned"]
        logs["scan_scope"] = "root"
        logs["ciphertext_keys"] = len(keys)
        logs["hits"] = [os.path.relpath(hit, root_text(root)) for hit in scanned["hits"]]
        logs["unreadable"] = [os.path.relpath(p, root_text(root)) for p in scanned["unreadable_paths"]]
        if logs_to is not None and has_dir(root, "logs"):
            if scanned["found"] is False and scanned["complete"] and not load_error:
                logs_base = os.path.join(root_text(root), "logs")
                log_files = [f for f in scanned["_files"] if f.startswith(logs_base + os.sep)]
                try:

                    exported = export_logs(root, logs_to, log_files, Detector(secrets, keys))
                    logs.update(
                        {
                            "files_copied": exported["files"],
                            "bytes_copied": exported["bytes"],
                            "export_scanned": True,
                            "copied": True,
                            "to": str(logs_to),
                        }
                    )
                except LeakFound:
                    logs["copy_error"] = "LeakFound"
                    logs["export_found"] = True
                except OSError as exc:
                    logs["copy_error"] = exc.__class__.__name__
            else:
                logs["refused"] = (
                    "token, key or ciphertext found in the sandbox root"
                    if scanned["found"]
                    else "secrets could not all be loaded or a file could not be read"
                )
    if load_error:
        logs["secret_load_error"] = load_error
    custody: dict = {"key_unlinked": None}
    volume: dict = {}
    if owned:


        priv, priv_error = (CREATED.get(run_id), None) if created else (None, None)
        if priv is None:
            try:
                priv = open_private(run_id)
            except (OSError, Refused) as exc:
                priv_error = f"{exc.__class__.__name__}: {exc}"
        ids = priv.ids if priv is not None else {}
        custody = unlink_key(root, ids)
        if entry_of(root) == "dir":
            volume = detach_volume(root)
            if volume.get("volume") != "still mounted":
                volume["root_removed"] = remove_root(root, ids.get("root"), priv)
        if priv is not None:
            if "root" in ids:
                remove_recorded(priv.fd, root_stage(ids), ids["root"], "dir")
            for job in ids.get("jobs", []):
                remove_recorded(priv.fd, str(job["name"]), job.get("id"), "file")
            if not on_own_volume(root):
                volume["image_removed"] = remove_recorded(priv.fd, IMAGE_NAME, ids.get("image"), "file") is not False
            if entry_of(root) is None:
                volume["private_removed"] = remove_private(priv)
            if not created:
                priv.close()
        else:
            volume["private"] = priv_error or "no private dir: nothing this run made can be proven, nothing deleted"
    time.sleep(0.5)
    rows = processes()
    lineage = own_lineage(rows)
    leftover = [
        {"pid": pid, "command_sha12": command_ref(cmd)}
        for pid, _, cmd in rows
        if (owned_process(cmd, root, run_id) or names_run(cmd, run_id)) and pid not in lineage
    ]
    result = {
        "labels_loaded": [name for name in (label, aux_label) if label_loaded(name)],
        "listeners": [{"port": port, "pids": pids} for port in ports if (pids := listener_pids(port))],
        "processes": leftover,
        "root_exists": entry_of(root) is not None,
        "volume_mounted": on_own_volume(root),
        "image_exists": image_exists(run_id),
        "private_exists": private_left(run_id),
        "bootout": booted,
        "logs": logs,
        "custody": custody,
        "volume": volume,
    }
    if refused:
        result["refused"] = refused
    result["clean"] = not (
        result["labels_loaded"]
        or result["listeners"]
        or result["processes"]
        or result["root_exists"]
        or result["volume_mounted"]
        or result["image_exists"]
        or result["private_exists"]
    )
    return result


def private_left(run_id: str) -> bool:

    try:
        return bool(private_names(run_id))
    except FileNotFoundError:
        return False
    except OSError:
        return True
