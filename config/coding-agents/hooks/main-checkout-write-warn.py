#!/usr/bin/env python3
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

WRITES = re.compile(r">|\btee\b|\bsed\s+[^\n;]*-[^\s]*i|\bgit\s+(?:apply|commit|add)\b")


def main_checkout(path):
    for parent in path.parents:
        dotgit = parent / ".git"
        if dotgit.is_dir():
            return parent
        if dotgit.exists():
            return None
    return None


def on_main(repo):
    try:
        head = (repo / ".git" / "HEAD").read_text().strip()
    except OSError:
        return False
    return head in {"ref: refs/heads/main", "ref: refs/heads/master"}


def candidate_repo(path):
    path = path.expanduser().resolve()
    if path.is_dir():
        return None
    repo = main_checkout(path)
    if repo is None:
        return None
    root = (
        Path(os.environ.get("REPOS_ROOT") or os.environ.get("GIT_GUARD_REPOS_ROOT") or repo.parent)
        .expanduser()
        .resolve()
    )
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return None
    if len(parts) < 2 or parts[0].endswith("-wt") or root / parts[0] != repo or ".git" in parts:
        return None
    return (repo, path) if on_main(repo) else None


def first_tracked(paths):
    found = {}
    for index, path in enumerate(paths):
        hit = candidate_repo(path)
        if hit:
            found.setdefault(hit[0], {}).setdefault(hit[1], index)
    tracked = []
    for repo, wanted in found.items():
        listed = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "-z", "--full-name", "--", *map(str, wanted)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if listed.returncode:
            continue
        names = {repo / name for name in listed.stdout.split("\0") if name}
        tracked.extend((wanted[path], repo) for path in wanted if path in names)
    return min(tracked)[1] if tracked else None


def refusal(paths):
    repo = first_tracked(paths)
    if repo is None:
        return None
    return (
        f"Warning: this writes a tracked file in the shared main checkout {repo}. "
        f"Use lane-worktree ensure --repo {repo.name} --branch <topic>, "
        "and edit the -wt worktree instead. Main checkouts belong to the merge step."
    )


def check(payload):
    tool = payload.get("tool_name")
    data = payload.get("tool_input")
    if not isinstance(data, dict):
        return None
    cwd = Path(payload.get("cwd") or Path.cwd())
    if tool in {"Edit", "Write"}:
        name = data.get("file_path")
        return refusal([cwd / name]) if isinstance(name, str) else None
    if tool != "Bash":
        return None
    command = data.get("command")
    if not isinstance(command, str) or not WRITES.search(command):
        return None
    try:
        words = shlex.split(command, comments=True)
    except ValueError:
        words = command.split()
    candidates = []
    for index, word in enumerate(words):
        if word in {"cd", "pushd"} and index + 1 < len(words):
            cwd = (cwd / words[index + 1]).resolve()
        if not word.startswith("-"):
            candidates.append(cwd / word.lstrip(">"))
        for name in re.findall(r"[\"']([^\"'\n]+)[\"']", word):
            candidates.append(cwd / name)
    return refusal(candidates)


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except ValueError:
        return
    if not isinstance(payload, dict):
        return
    try:
        advice = check(payload)
    except (OSError, ValueError, subprocess.SubprocessError):
        advice = None
    if advice:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "additionalContext": advice,
                    }
                }
            )
        )


if __name__ == "__main__":
    main()
    if os.environ.get("HOOK_FLOOR_RECEIPT"):
        print("floor-ok main-checkout-write-warn.py")
