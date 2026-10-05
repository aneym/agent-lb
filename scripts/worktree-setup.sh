#!/bin/sh
# Run from any directory; existing local environments are deliberately retained.
exec python3 - "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)" <<'PY'
import fcntl
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

root = Path(sys.argv[1])
local = root / ".venv"


def log(message):
    print(f"worktree-setup: {message}", file=sys.stderr)


def run(argv, **kwargs):
    return subprocess.run(argv, cwd=root, timeout=240, check=True, **kwargs)


def fallback(reason):
    log(f"shared environment unavailable ({reason}); keeping local setup")
    if local.is_symlink():
        log("existing shared link retained; not syncing into another worktree's environment")
        return
    try:
        env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(local))
        run(["uv", "sync", "--frozen"], env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"local uv sync unavailable: {exc}")


if local.exists() and not local.is_symlink():
    log("existing local .venv retained; remove or move it explicitly to opt into sharing")
    sys.exit(0)

try:
    if not shutil.which("uv"):
        raise RuntimeError("uv is missing")
    python = run(["uv", "python", "find"], capture_output=True, text=True).stdout.strip()
    version = run([python, "-c", "import sys; print(sys.version)"], capture_output=True, text=True).stdout
    digest = hashlib.sha256((root / "uv.lock").read_bytes() + version.encode()).hexdigest()
    cache = Path.home() / ".cache" / "agent-lb-venvs"
    cache.mkdir(parents=True, exist_ok=True)
    shared = cache / digest
    with (cache / f"{digest}.lock").open("a") as lock:
        deadline = time.monotonic() + 240
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("environment lock timed out")
                time.sleep(0.2)
        ready = shared / ".worktree-ready"
        if not ready.exists():
            env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(shared))
            # Never install an editable project pointing at the builder's worktree.
            run(["uv", "sync", "--frozen", "--no-install-project", "--python", python], env=env)
            run([str(shared / "bin/python"), "-I", "-c",
                 "import importlib.util; assert importlib.util.find_spec('app') is None"])
            ready.touch()
        temporary = root / f".venv-link-{os.getpid()}"
        try:
            temporary.symlink_to(shared, target_is_directory=True)
            temporary.replace(local)
        finally:
            temporary.unlink(missing_ok=True)
    log(f".venv -> {shared} (run Python modules from this worktree)")
except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
    fallback(exc)
PY
