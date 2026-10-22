#!/usr/bin/env python3
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

WRITES = re.compile(
    r">|\b(?:tee|cp|mv|rm|touch|install|truncate|patch|apply_patch)\b|"
    r"\bsed\s+[^\n;]*-[^\s]*i|\bperl\s+[^\n;]*-[^\s]*i|"
    r"write_text|write_bytes|writeFile|open\s*\([^\n]*[\"\'][wax+][bat+]*[\"\']"
)


def git_out(*args):
    done = subprocess.run(["git", *args], capture_output=True, text=True, timeout=5, check=False)
    return None if done.returncode else done.stdout.strip()


def common_dir(start):
    if not start.is_dir():
        return None
    if any(name in os.environ for name in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_CEILING_DIRECTORIES")):
        found = git_out("-C", str(start), "rev-parse", "--path-format=absolute", "--git-common-dir")
        return Path(found) if found else None
    for folder in (start, *start.parents):
        dot = folder / ".git"
        if dot.is_dir():
            return dot.resolve()
        if dot.is_file():
            text = dot.read_text(errors="replace").strip()
            if not text.startswith("gitdir:"):
                return None
            gitdir = (folder / text[len("gitdir:") :].strip()).resolve()
            if not gitdir.is_dir():
                return None
            shared = gitdir / "commondir"
            return (gitdir / shared.read_text().strip()).resolve() if shared.is_file() else gitdir
    return None


def tracked_main(path):
    path = path.expanduser().resolve()
    if path.is_dir():
        return None
    common = common_dir(path.parent)
    if common is None:
        return None
    repo = common.parent
    root = (
        Path(os.environ.get("REPOS_ROOT") or os.environ.get("GIT_GUARD_REPOS_ROOT") or repo.parent)
        .expanduser()
        .resolve()
    )
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return None
    if len(parts) < 2 or parts[0].endswith("-wt") or root / parts[0] != repo:
        return None
    if not (repo / ".git").is_dir():
        return None
    head = (repo / ".git" / "HEAD").read_text(errors="replace").strip()
    if head not in {"ref: refs/heads/main", "ref: refs/heads/master"}:
        return None
    tracked = git_out("-C", str(repo), "ls-files", "--", str(path))
    return repo if tracked else None


def refusal(path):
    repo = tracked_main(path)
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
        return refusal(cwd / name) if isinstance(name, str) else None
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
    for path in candidates:
        message = refusal(path)
        if message:
            return message
    return None


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
