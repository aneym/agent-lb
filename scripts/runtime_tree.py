from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path

ANYWHERE = (
    ".git",
    ".venv",
    "__pycache__",
    "*.pyc",
    ".DS_Store",
    "node_modules",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
)
TOP = (
    ".agent-lb",
    ".codex-lb",
    ".env",
    ".env.local",
    ".env.*.local",
    ".envrc",
    ".local",
    "certs",
    "tests",
    "dist",
    "build",
    "features/.gen",
    ".agents/hooks/state",
    "clients/macos-menubar/.build",
    "clients/macos-menubar/AgentLB.app",
    ".follow-rev",
)
STATIC = "app/static"


@dataclass(frozen=True)
class Paths:
    src: Path
    root: Path
    runtime: Path
    bin_dir: Path
    uv: str
    bun: str

    @property
    def stage(self) -> Path:
        return self.root / "follow-stage"

    @property
    def build(self) -> Path:
        return self.root / "follow-build"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def log(self) -> Path:
        return self.root / "sync.log"

    @property
    def state(self) -> Path:
        return self.root / "follow-state.json"

    @classmethod
    def from_env(cls) -> Paths:
        home = Path.home()
        runtime = Path(os.environ.get("AGENT_LB_RUNTIME_DIR") or home / ".agent-lb/runtime/agent-lb")
        return cls(
            src=Path(os.environ.get("AGENT_LB_SYNC_SRC") or "/Volumes/StudioExt/repos/agent-lb"),
            root=runtime.parent,
            runtime=runtime,
            bin_dir=Path(os.environ.get("AGENT_LB_BIN_DIR") or home / ".agent-lb/bin"),
            uv=os.environ.get("AGENT_LB_SYNC_UV") or "/opt/homebrew/bin/uv",
            bun=os.environ.get("AGENT_LB_FOLLOW_BUN") or shutil.which("bun") or str(home / ".bun/bin/bun"),
        )


def utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def log_line(paths: Paths, text: str) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    with paths.log.open("a") as fh:
        fh.write(f"{utc()} {text}\n")


def run(
    cmd: list[str], *, cwd: Path | None = None, timeout: float = 120, log: Path | None = None
) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if log is None:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    with log.open("a") as fh:
        return subprocess.run(cmd, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT, text=True, timeout=timeout, env=env)


def git(paths: Paths, *args: str, timeout: float = 60) -> str:
    done = run(["git", "-C", str(paths.src), *args], timeout=timeout)
    if done.returncode:
        lines = (done.stderr or "").strip().splitlines()
        raise RuntimeError(lines[-1] if lines else f"git {args[0]} failed")
    return done.stdout.strip()


def fetch_main(paths: Paths) -> str | None:
    try:
        git(paths, "fetch", "-q", "origin", "+refs/heads/main:refs/remotes/origin/main", timeout=90)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        return str(exc)[:200]
    return None


def origin_main(paths: Paths) -> str:
    return git(paths, "rev-parse", "--verify", "refs/remotes/origin/main^{commit}")


def tree_of(paths: Paths, sha: str, path: str) -> str:
    try:
        return git(paths, "rev-parse", f"{sha}:{path}")
    except RuntimeError:
        return ""


def export(paths: Paths, sha: str, dest: Path) -> None:
    fresh = dest.with_name(dest.name + ".new")
    shutil.rmtree(fresh, ignore_errors=True)
    fresh.mkdir(parents=True)
    archive = fresh.with_suffix(".tar")
    with archive.open("wb") as fh:
        done = subprocess.run(
            ["git", "-C", str(paths.src), "archive", "--format=tar", sha],
            stdout=fh,
            stderr=subprocess.PIPE,
            timeout=300,
        )
    if done.returncode:
        archive.unlink(missing_ok=True)
        raise RuntimeError(f"git archive {sha[:12]} failed: {done.stderr.decode(errors='replace').strip()[-200:]}")
    with tarfile.open(archive) as tar:
        tar.extractall(fresh, filter="tar")
    archive.unlink(missing_ok=True)
    (fresh / ".follow-rev").write_text(sha + "\n")
    old = dest.with_name(dest.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if dest.exists():
        dest.rename(old)
    fresh.rename(dest)
    shutil.rmtree(old, ignore_errors=True)


def staged_rev(paths: Paths) -> str:
    try:
        return (paths.stage / ".follow-rev").read_text().strip()
    except OSError:
        return ""


def rsync_args(*, manage_static: bool) -> list[str]:
    args = ["rsync", "-a", "--delete", "--checksum"]
    for pattern in ANYWHERE:
        args += ["--exclude", pattern]
    for pattern in TOP:
        args += ["--exclude", "/" + pattern]
    if not manage_static:
        args += ["--exclude", "/" + STATIC]
    return args


def content_changes(itemized: str) -> list[str]:
    changed = []
    for line in itemized.splitlines():
        if not line.strip():
            continue
        if line.startswith("*deleting"):
            changed.append(line)
            continue
        flags, _, name = line.partition(" ")
        if len(flags) < 9 or flags[1] == "d":
            continue
        if flags[0] in "<>ch" or "+" in flags or flags[2] == "c" or flags[3] == "s":
            changed.append(f"{flags} {name.strip()}")
    return changed


def diff(paths: Paths, *, manage_static: bool) -> list[str]:
    done = run(
        [*rsync_args(manage_static=manage_static), "-n", "--itemize-changes", f"{paths.stage}/", f"{paths.runtime}/"],
        timeout=240,
    )
    if done.returncode:
        raise RuntimeError(f"rsync dry run rc={done.returncode}: {(done.stderr or '').strip()[-200:]}")
    return content_changes(done.stdout)


def apply(paths: Paths, *, manage_static: bool) -> None:
    done = run(
        [*rsync_args(manage_static=manage_static), f"{paths.stage}/", f"{paths.runtime}/"], timeout=600, log=paths.log
    )
    if done.returncode:
        raise RuntimeError(f"rsync into runtime rc={done.returncode}")


def apply_live(paths: Paths, rels: list[str], deleted: list[str]) -> None:
    for rel in [*rels, *deleted]:
        target = paths.runtime / rel
        if Path(rel).is_absolute() or ".." in Path(rel).parts or ".venv" in Path(rel).parts:
            raise RuntimeError(f"unsafe live path: {rel}")
        if not target.parent.resolve().is_relative_to(paths.runtime.resolve()):
            raise RuntimeError(f"live path escapes runtime: {rel}")
    if rels:
        done = subprocess.run(
            ["rsync", "-a", "--checksum", "--files-from=-", f"{paths.stage}/", f"{paths.runtime}/"],
            input="\n".join(rels) + "\n",
            capture_output=True,
            text=True,
            timeout=600,
        )
        if done.returncode:
            raise RuntimeError(f"live rsync rc={done.returncode}: {done.stderr.strip()[-200:]}")
    for rel in sorted(deleted, key=lambda name: name.count("/"), reverse=True):
        target = paths.runtime / rel
        if target.is_dir() and not target.is_symlink():
            target.rmdir()
        else:
            target.unlink(missing_ok=True)


def backup(paths: Paths, label: str) -> Path:
    dest = paths.backups / f"{label}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    (dest / "runtime").mkdir(parents=True, exist_ok=True)
    done = run(
        [*rsync_args(manage_static=True), f"{paths.runtime}/", f"{dest / 'runtime'}/"], timeout=600, log=paths.log
    )
    if done.returncode:
        raise RuntimeError(f"backup rsync rc={done.returncode}")
    return dest


def restore(paths: Paths, dest: Path) -> None:
    done = run(
        [*rsync_args(manage_static=True), f"{dest / 'runtime'}/", f"{paths.runtime}/"], timeout=600, log=paths.log
    )
    if done.returncode:
        raise RuntimeError(f"restore rsync rc={done.returncode}")


def prune_backups(paths: Paths, label: str, keep: int = 5) -> None:
    found = sorted(p for p in paths.backups.glob(f"{label}-*") if p.is_dir())
    for old in found[:-keep]:
        shutil.rmtree(old, ignore_errors=True)


def lock_sha(runtime: Path) -> str:
    try:
        return hashlib.sha256((runtime / "uv.lock").read_bytes()).hexdigest()
    except OSError:
        return ""


def venv_current(paths: Paths) -> bool:
    stamp = paths.runtime / ".venv" / ".uv-lock-sha"
    try:
        recorded = stamp.read_text().strip()
    except OSError:
        recorded = ""
    return (paths.runtime / ".venv/bin/agent-lb").exists() and recorded == lock_sha(paths.runtime)


def sync_venv(paths: Paths) -> None:
    if venv_current(paths):
        return
    done = run([paths.uv, "sync", "--frozen"], cwd=paths.runtime, timeout=900, log=paths.log)
    if done.returncode:
        raise RuntimeError(f"uv sync --frozen rc={done.returncode}")
    (paths.runtime / ".venv").mkdir(exist_ok=True)
    (paths.runtime / ".venv" / ".uv-lock-sha").write_text(lock_sha(paths.runtime) + "\n")
    log_line(paths, "venv rebuilt (uv.lock changed)")


def build_frontend(paths: Paths) -> None:
    src = paths.stage / "frontend"
    work = paths.build / "frontend"
    work.mkdir(parents=True, exist_ok=True)
    done = run(["rsync", "-a", "--delete", "--exclude", "/node_modules", f"{src}/", f"{work}/"], timeout=240)
    if done.returncode:
        raise RuntimeError(f"frontend copy rc={done.returncode}")
    out = paths.build / STATIC
    shutil.rmtree(out, ignore_errors=True)
    for step in ([paths.bun, "install", "--frozen-lockfile"], [paths.bun, "run", "build"]):
        done = run(step, cwd=work, timeout=900, log=paths.log)
        if done.returncode:
            raise RuntimeError(f"frontend {' '.join(step[1:])} rc={done.returncode}")
    if not (out / "index.html").is_file():
        raise RuntimeError(f"frontend build wrote no {STATIC}/index.html")


def stage_static(paths: Paths) -> bool:
    built = paths.build / STATIC
    if not (built / "index.html").is_file():
        return False
    dest = paths.stage / STATIC
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(built, dest, symlinks=True)
    return True
