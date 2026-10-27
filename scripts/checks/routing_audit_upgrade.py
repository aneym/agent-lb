from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GIT = "/usr/bin/git" if Path("/usr/bin/git").is_file() else "git"
AUDIT_MODULES = (
    "routing_audit_metrics.py",
    "routing_audit_outcomes.py",
    "routing_audit_reports.py",
    "routing_audit_sources.py",
    "routing_audit_values.py",
)
RUNTIME_FILES = (
    "scripts/runtime_bin.py",
    "scripts/runtime_follow.py",
    "scripts/runtime_plan.py",
    "scripts/runtime_tree.py",
    "scripts/routing_audit_manifest.py",
)


def run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    done = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, timeout=120)
    if done.returncode:
        detail = (done.stderr or done.stdout).decode(errors="replace")[-800:]
        raise AssertionError(f"{cmd[0]} rc={done.returncode}: {detail}")
    return done


def require(ok: bool, message: str) -> None:
    if not ok:
        raise AssertionError(message)


def commit(src: Path, message: str) -> None:
    run([GIT, "add", "."], cwd=src)
    run([GIT, "commit", "-m", message], cwd=src)
    run([GIT, "push", "origin", "main"], cwd=src)


def exercise(source: Path, root: Path) -> None:
    origin, src, runtime, bins = (root / name for name in ("origin", "src", "runtime/agent-lb", "bin"))
    run([GIT, "init", "--bare", "--initial-branch=main", str(origin)], cwd=root)
    run([GIT, "clone", str(origin), str(src)], cwd=root)
    for key, value in (("user.name", "Routing Audit Upgrade Check"), ("user.email", "audit-upgrade@example.invalid")):
        run([GIT, "config", key, value], cwd=src)
    for rel in ("pyproject.toml", "uv.lock"):
        dest = src / rel
        dest.write_bytes((source / rel).read_bytes())
    old_audit = run([GIT, "show", "f0bf9b1d^:scripts/routing_daily_audit.py"], cwd=source).stdout
    target = src / "scripts/routing_daily_audit.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(old_audit)
    commit(src, "install pre-split audit")
    archive = root / "old.tar"
    with archive.open("wb") as fh:
        done = subprocess.run([GIT, "archive", "HEAD"], cwd=src, stdout=fh, stderr=subprocess.PIPE, timeout=120)
    require(done.returncode == 0, done.stderr.decode(errors="replace"))
    runtime.mkdir(parents=True)
    with tarfile.open(archive) as tar:
        tar.extractall(runtime, filter="tar")
    bins.mkdir()
    (bins / "routing_daily_audit.py").write_bytes(old_audit)

    payload = [*RUNTIME_FILES, "scripts/routing_daily_audit.py", *(f"scripts/{name}" for name in AUDIT_MODULES)]
    for rel in payload:
        staged = source / rel
        if not staged.is_file():
            continue
        dest = src / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(staged.read_bytes())
    commit(src, "split audit into sibling modules")

    env = {
        **os.environ,
        "AGENT_LB_RUNTIME_DIR": str(runtime),
        "AGENT_LB_SYNC_SRC": str(src),
        "AGENT_LB_BIN_DIR": str(bins),
        "PATH": f"{Path.home()}/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    }
    env.pop("AGENT_LB_FOLLOW_ALERT_TO", None)
    run([sys.executable, str(source / "scripts/runtime_follow.py"), "tick"], cwd=root, env=env)
    for name in AUDIT_MODULES:
        installed = bins / name
        require(installed.is_file(), f"upgrade did not install {name}")
        require(installed.read_bytes() == (source / "scripts" / name).read_bytes(), f"installed {name} is stale")
    helped = run([sys.executable, str(bins / "routing_daily_audit.py"), "--help"], cwd=root)
    usage = helped.stdout.decode(errors="replace")
    require("usage: routing_daily_audit.py" in usage, "upgraded audit did not produce its CLI help")
    print("ok: runtime-follow upgraded pre-split audit and installed " + ", ".join(AUDIT_MODULES))
    print(usage.splitlines()[0])


def main() -> int:
    source = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else REPO
    try:
        with tempfile.TemporaryDirectory(prefix="routing-audit-upgrade-") as temp:
            exercise(source, Path(temp))
    except (AssertionError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL routing audit upgrade: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
