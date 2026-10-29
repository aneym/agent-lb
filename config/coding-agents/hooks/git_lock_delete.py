import stat
from pathlib import Path

from git_lock_recovery import recover  # type: ignore[missing-import]


def stale_worktree_lock(value):
    path = Path(value)
    if not path.is_absolute() or path.resolve() != path:
        return False
    if path.parent.parent.name != "worktrees" or path.parent.parent.parent.name != ".git":
        return False
    if not path.name.endswith(".lock"):
        return False
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size:
            return False
        common = path.parent.parent.parent
        if not (path.parent / "gitdir").is_file() or not (common / "config").is_file():
            return False
        records = recover(common, apply=False, paths=[path], actor="rm-dynamic-deny")
        return len(records) == 1 and records[0].get("action") == "would_remove"
    except (OSError, ValueError):
        return False
