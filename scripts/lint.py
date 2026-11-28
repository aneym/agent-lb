#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_ROOTS = ("app", "clients", "config", "scripts", "tests")
PROXY_ARCH_PREFIXES = (
    "app/modules/proxy/service.py",
    "app/modules/proxy/_service/",
    "scripts/check_proxy_architecture.py",
)


def _git_lines(*args: str) -> list[str]:
    result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _changed_files(mode: str, base: str) -> list[str]:
    if mode == "staged":
        return _git_lines("diff", "--cached", "--name-only", "--diff-filter=ACMR")
    files = set(_git_lines("diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD"))
    files.update(_git_lines("diff", "--name-only", "--diff-filter=ACMR"))
    files.update(_git_lines("diff", "--cached", "--name-only", "--diff-filter=ACMR"))
    files.update(_git_lines("ls-files", "--others", "--exclude-standard"))
    return sorted(files)


def _venv_roots() -> list[Path]:
    roots = [ROOT]
    common = _git_lines("rev-parse", "--git-common-dir")
    if common:
        common_path = Path(common[0])
        if not common_path.is_absolute():
            common_path = (ROOT / common_path).resolve()
        parent = common_path.parent
        if parent not in roots:
            roots.append(parent)
    return roots


def _find_ruff() -> list[str]:
    for root in _venv_roots():
        venv_ruff = root / ".venv" / "bin" / "ruff"
        if venv_ruff.is_file() and os.access(venv_ruff, os.X_OK):
            return [str(venv_ruff)]
    if found := shutil.which("ruff"):
        return [found]
    if uv := shutil.which("uv"):
        return [uv, "run", "--no-sync", "ruff"]
    raise RuntimeError("ruff not found (expected .venv/bin/ruff, ruff on PATH, or uv)")


def _find_modcheck() -> str | None:
    if found := shutil.which("modcheck"):
        return found
    for base in (
        os.environ.get("AGENT_RAILS_GATE_DIR", ""),
        str(Path.home() / "repos" / "agent-rails" / "tools" / "gate"),
        "/Volumes/StudioExt/repos/agent-rails/tools/gate",
    ):
        if base:
            candidate = Path(base) / "modcheck"
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return None


def _find_python() -> str:
    for root in _venv_roots():
        venv_python = root / ".venv" / "bin" / "python"
        if venv_python.is_file() and os.access(venv_python, os.X_OK):
            return str(venv_python)
    return sys.executable


def _run(label: str, cmd: list[str], *, cwd: Path = ROOT) -> int:
    result = subprocess.run(cmd, cwd=cwd, check=False)
    if result.returncode != 0:
        print(f"lint: {label} failed (exit {result.returncode})", file=sys.stderr)
    return result.returncode


def _is_python_target(rel: str) -> bool:
    if not rel.endswith(".py"):
        return False
    return rel.split("/", 1)[0] in PYTHON_ROOTS and (ROOT / rel).is_file()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Native lint runner for agent-lb.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--changed", action="store_true")
    group.add_argument("--staged", action="store_true")
    group.add_argument("--all", action="store_true")
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args(argv)
    mode = "staged" if args.staged else "all" if args.all else "changed"

    changed = [] if mode == "all" else _changed_files(mode, args.base)
    rc = 0

    try:
        ruff_cmd = _find_ruff()
    except RuntimeError as exc:
        print(f"lint: {exc}", file=sys.stderr)
        return 1

    if mode == "all":
        py_targets = [root for root in PYTHON_ROOTS if (ROOT / root).exists()]
    else:
        py_targets = [rel for rel in changed if _is_python_target(rel)]

    if py_targets:
        rc |= _run("ruff check", [*ruff_cmd, "check", *py_targets])

    if modcheck := _find_modcheck():
        if mode == "staged":
            mc_args = [modcheck, "--staged", "--repo", str(ROOT)]
        elif mode == "all":
            mc_args = [modcheck, "--tree", "--repo", str(ROOT)]
        else:
            mc_args = [modcheck, "--base", args.base, "--rev", "HEAD", "--repo", str(ROOT)]
        rc |= _run("modcheck", mc_args)

    proxy_touched = mode == "all" or any(
        rel == prefix or rel.startswith(prefix) for rel in changed for prefix in PROXY_ARCH_PREFIXES
    )
    if proxy_touched:
        rc |= _run("proxy-architecture", [_find_python(), str(ROOT / "scripts" / "check_proxy_architecture.py")])

    frontend_dir = ROOT / "frontend"
    frontend_touched = mode == "all" or any(rel.startswith("frontend/") for rel in changed)
    if frontend_touched and (frontend_dir / "node_modules").is_dir():
        oxlint = frontend_dir / "node_modules" / ".bin" / "oxlint"
        if oxlint.is_file():
            rc |= _run("oxlint", [str(oxlint), "src"], cwd=frontend_dir)
        if bun := shutil.which("bun"):
            rc |= _run("frontend typecheck", [bun, "run", "typecheck"], cwd=frontend_dir)

    if shellcheck := shutil.which("shellcheck"):
        sh_targets = (
            [str(p.relative_to(ROOT)) for p in ROOT.glob("scripts/*.sh")]
            if mode == "all"
            else [rel for rel in changed if rel.endswith(".sh") and (ROOT / rel).is_file()]
        )
        if sh_targets:
            rc |= _run("shellcheck", [shellcheck, *sh_targets])

    return 1 if rc else 0


if __name__ == "__main__":
    raise SystemExit(main())
