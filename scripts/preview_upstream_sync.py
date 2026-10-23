#!/usr/bin/env python3
"""Inventory an upstream merge without changing the index or working tree."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def paths(*args: str) -> list[str]:
    return [path for path in git(*args).split("\0") if path]


def migrations(ref: str) -> set[str]:
    return {
        path
        for path in paths("ls-tree", "-rz", "--name-only", ref, "--", "app/db/alembic/versions")
        if path.endswith(".py") and not path.endswith("/__init__.py")
    }


def preview(fork_ref: str, upstream_ref: str, include_paths: bool) -> dict:
    fork_sha = git("rev-parse", "--verify", f"{fork_ref}^{{commit}}")
    upstream_sha = git("rev-parse", "--verify", f"{upstream_ref}^{{commit}}")
    merge_base = git("merge-base", fork_sha, upstream_sha)
    fork_only, upstream_only = (
        int(count)
        for count in git("rev-list", "--left-right", "--count", f"{fork_sha}...{upstream_sha}").split()
    )
    result = subprocess.run(
        ["git", "merge-tree", "--write-tree", "--name-only", "-z", "--no-messages", fork_sha, upstream_sha],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.strip() or "git merge-tree failed")
    fields = [field for field in result.stdout.split("\0") if field]
    if not fields:
        raise RuntimeError("git merge-tree returned no preview tree")
    conflicts = fields[1:]
    if bool(conflicts) != (result.returncode == 1):
        raise RuntimeError("git merge-tree conflict status and output disagree")
    upstream_paths = paths("diff", "--name-only", "-z", merge_base, upstream_sha)
    fork_migrations = migrations(fork_sha)
    upstream_migrations = migrations(upstream_sha)
    inventory = {
        "fork": {"ref": fork_ref, "sha": fork_sha},
        "upstream": {"ref": upstream_ref, "sha": upstream_sha},
        "merge_base": merge_base,
        "fork_only_commits": fork_only,
        "upstream_only_commits": upstream_only,
        "upstream_changed_paths": len(upstream_paths),
        "upstream_changes_by_directory": dict(sorted(Counter(path.split("/")[0] for path in upstream_paths).items())),
        "merge_would_conflict": bool(conflicts),
        "conflicted_paths": len(conflicts),
        "conflicts_by_directory": dict(sorted(Counter(path.split("/")[0] for path in conflicts).items())),
        "migrations": {
            "fork": len(fork_migrations),
            "upstream": len(upstream_migrations),
            "fork_only": len(fork_migrations - upstream_migrations),
            "upstream_only": len(upstream_migrations - fork_migrations),
        },
    }
    if include_paths:
        inventory["conflict_paths"] = conflicts
        inventory["upstream_changed_path_list"] = upstream_paths
        inventory["migrations"]["fork_only_paths"] = sorted(fork_migrations - upstream_migrations)
        inventory["migrations"]["upstream_only_paths"] = sorted(upstream_migrations - fork_migrations)
    return inventory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fork-ref", default="origin/main")
    parser.add_argument("--upstream-ref", default="v1.24.0")
    parser.add_argument("--include-paths", action="store_true")
    args = parser.parse_args()
    try:
        inventory = preview(args.fork_ref, args.upstream_ref, args.include_paths)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        detail = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) else str(error)
        print(f"upstream preview failed: {detail}", file=sys.stderr)
        return 2
    print(json.dumps(inventory, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
