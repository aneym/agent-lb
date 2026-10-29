import os
import re
import subprocess
import time
from pathlib import Path

EXPORT = re.compile(r"^coding-agents-sync-[a-z0-9_]{8}$")
TRASH = ".coding-agents-sync-trash"


def git(clone, args):
    return subprocess.run(
        ["git", "-C", str(clone)] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        env=dict(os.environ, GIT_TERMINAL_PROMPT="0"),
        close_fds=True,
    )


def registered(clone, lb):
    admin = clone / ".git/worktrees"
    found = {}
    if not admin.is_dir():
        return found
    for entry in admin.iterdir():
        try:
            gitdir = Path((entry / "gitdir").read_text().strip())
        except OSError:
            continue
        if gitdir.parent.parent == lb and EXPORT.fullmatch(gitdir.parent.name):
            found[gitdir.parent] = entry
    return found


def ours(path, clone):
    try:
        line = (path / ".git").read_text().strip()
    except OSError:
        return False
    return line.startswith("gitdir: ") and Path(line[8:]).parent == clone / ".git/worktrees"


def remove(path, deadline):
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files + [d for d in dirs if os.path.islink(os.path.join(root, d))]:
            if time.monotonic() >= deadline:
                return False
            os.unlink(os.path.join(root, name))
        for name in dirs:
            child = os.path.join(root, name)
            if not os.path.islink(child):
                if time.monotonic() >= deadline:
                    return False
                os.rmdir(child)
    os.rmdir(path)
    return True


def prune_exports(clone, lb, warnings, budget=20.0):
    trash = lb / TRASH
    try:
        owned = registered(clone, lb)
        moved = 0
        for path, entry in owned.items():
            (entry / "locked").unlink(missing_ok=True)
            if path.is_dir() and not path.is_symlink():
                trash.mkdir(exist_ok=True)
                os.rename(path, trash / path.name)
                moved += 1
        for path in lb.iterdir():
            if EXPORT.fullmatch(path.name) and path.is_dir() and not path.is_symlink() and ours(path, clone):
                trash.mkdir(exist_ok=True)
                os.rename(path, trash / path.name)
                moved += 1
        if owned or moved:
            git(clone, ["worktree", "prune"])
        deadline = time.monotonic() + budget
        left = 0
        for path in sorted(trash.iterdir()) if trash.is_dir() else []:
            if time.monotonic() >= deadline or not remove(path, deadline):
                left += 1
        if trash.is_dir() and not any(trash.iterdir()):
            trash.rmdir()
        if moved or left:
            warnings.append("pruned %d export worktrees; %d left to delete" % (moved, left))
    except (OSError, subprocess.SubprocessError) as exc:
        warnings.append("export prune: %s" % exc)
