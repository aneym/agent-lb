from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .filesystem import (
    confined,
    open_in_root,
    write_in_root,
    write_json,
)
from .launchd import (
    plist_paths,
)
from .paths import (
    DISABLED_SCHEDULERS,
    DRAIN_S,
    LIVE_RUNTIME,
    LIVE_VENV,
    MANAGED_ROUTING,
    REAL_HOME,
    Refused,
    Unhealthy,
    bootstrap_script,
    labels_for,
)
from .private_storage import (
    Private,
    remove_private,
    remove_recorded,
    root_stage,
)
from .protection import (
    interpreter_for,
)


def sandbox_env(root: Path, ports: dict[str, int], run_id: str, transport: str) -> dict[str, str]:

    env = {
        "HOME": str(root / "home"),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "PYTHONPATH": str(root / "runtime"),
        "LB_SANDBOX_ROOT": str(root),
        "AGENT_LB_DATA_DIR": str(root / "data"),
        "AGENT_LB_DATABASE_URL": f"sqlite+aiosqlite:///{root / 'data' / 'store.db'}",
        "AGENT_LB_DATABASE_SQLITE_PRE_MIGRATE_BACKUP_ENABLED": "false",
        "AGENT_LB_ENCRYPTION_KEY_FILE": str(root / "data" / "encryption.key"),
        "AGENT_LB_LOCAL_INSTANCE_ID": f"sbx-{run_id}",
        "AGENT_LB_FEDERATION_PEER_URL": f"http://127.0.0.1:{ports['gate']}",
        "AGENT_LB_FEDERATION_MIRROR_INTERVAL_SECONDS": "5",
        "AGENT_LB_FEDERATION_FORWARD_REQUEST_LOGS": "false",
        "AGENT_LB_FEDERATION_PUSH_PATH": str(root / "data" / "federation-push.json"),
        "AGENT_LB_UPSTREAM_BASE_URL": f"http://127.0.0.1:{ports['edge_openai']}",
        "AGENT_LB_ANTHROPIC_UPSTREAM_BASE_URL": f"http://127.0.0.1:{ports['edge_anthropic']}",
        "AGENT_LB_UPSTREAM_STREAM_TRANSPORT": transport,
        "AGENT_LB_DASHBOARD_AUTH_MODE": "trusted_header",
        "AGENT_LB_FIREWALL_TRUST_PROXY_HEADERS": "true",
        "AGENT_LB_FIREWALL_TRUSTED_PROXY_CIDRS": "192.0.2.1/32",
        "FORWARDED_ALLOW_IPS": "",
        "AGENT_LB_PROXY_UNAUTHENTICATED_CLIENT_CIDRS": "127.0.0.1/32",
        "AGENT_LB_CONVERSATION_ARCHIVE_DIR": str(root / "data" / "conversation-archive"),
        "UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN": str(DRAIN_S),
    }
    for name in DISABLED_SCHEDULERS:
        env[f"AGENT_LB_{name}_ENABLED"] = "false"
    return env


def plist_bodies(
    root: Path, run_id: str, ports: dict[str, int], transport: str, script: Path | None = None
) -> tuple[dict, dict]:

    label, aux_label = labels_for(run_id)
    python = str(interpreter_for(root))
    script_text = str(script or bootstrap_script())
    primary = {
        "Label": label,
        "ProgramArguments": [
            python,
            "-I",
            script_text,
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
        "ProgramArguments": [python, "-I", script_text, "_aux", str(root)],
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


def cleanup_unpublished(priv: Private) -> dict:

    out: dict = {}
    if "root" in priv.ids:
        out["stage_removed"] = remove_recorded(priv.fd, root_stage(priv.ids), priv.ids["root"], "dir")
    out["private_removed"] = remove_private(priv)
    return out


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
