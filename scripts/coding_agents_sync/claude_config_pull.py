import os
import subprocess
from pathlib import Path

from claude_config_alert import notify  # type: ignore[missing-import]

ORIGINS = (
    "https://github.com/aneym/claude-config.git",
    "https://github.com/aneym/claude-config",
    "git@github.com:aneym/claude-config.git",
)
MARKERS = Path(".agent-lb/managed/coding-agents")
GENERATED_DIRS = ("agents", "hooks")


class Blocked(Exception):
    def __init__(self, result, detail):
        super().__init__(detail)
        self.result, self.detail = result, detail


def probe(repo, *args, timeout=60):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Blocked("failed", f"git {args[0]}: {exc}")
    return proc.returncode, proc.stdout.rstrip("\n"), proc.stderr.strip()


def git(repo, *args, timeout=60):
    code, out, err = probe(repo, *args, timeout=timeout)
    if code:
        raise Blocked("failed", f"git {args[0]}: {(err or f'exit {code}')[-500:]}")
    return out


def generated(home, path):
    parts = Path(path).parts
    if len(parts) != 2 or parts[0] not in GENERATED_DIRS:
        return False
    stem = Path(parts[1]).stem
    try:
        return (home / MARKERS / stem).read_text() == f"agent-lb:{stem}:v1\n"
    except (OSError, UnicodeError):
        return False


def dirty_paths(repo):
    entries = git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=no").split("\0")
    paths = []
    while entries:
        entry = entries.pop(0)
        if not entry:
            continue
        paths.append(entry[3:])
        if entry[0] in "RC" and entries:
            paths.append(entries.pop(0))
    return paths


def advance(home, repo):
    if not (repo / ".git").exists():
        return {"result": "absent"}
    code, origin, _ = probe(repo, "remote", "get-url", "origin")
    if code or origin not in ORIGINS:
        return {"result": "absent"}
    code, branch, _ = probe(repo, "symbolic-ref", "-q", "--short", "HEAD")
    if code or branch != "main":
        raise Blocked("off_main", f"~/.claude is on {branch or 'a detached HEAD'}, not main")
    git(
        repo,
        "fetch",
        "-q",
        "origin",
        "+refs/heads/main:refs/remotes/origin/main",
        timeout=120,
    )
    head = git(repo, "rev-parse", "HEAD")
    target = git(repo, "rev-parse", "refs/remotes/origin/main^{commit}")
    if head == target:
        return {"result": "current", "commit": head}
    if probe(repo, "merge-base", "--is-ancestor", head, target)[0]:
        raise Blocked("diverged", f"HEAD {head[:12]} has commits origin/main {target[:12]} lacks")
    dirty = dirty_paths(repo)
    kept = sorted(path for path in dirty if not generated(home, path))
    if kept:
        more = f" and {len(kept) - 20} more" if len(kept) > 20 else ""
        raise Blocked("dirty", f"uncommitted edits block the pull: {', '.join(kept[:20])}{more}")
    incoming = set(git(repo, "diff", "--name-only", "-z", head, target).split("\0"))
    regenerate = sorted(set(dirty) & incoming)
    untracked = rendered_removals(home, repo, head, target)
    if regenerate:
        git(repo, "checkout", "-q", head, "--", *regenerate)
    git(repo, "merge", "-q", "--ff-only", target, timeout=120)
    for path, (data, mode) in untracked.items():
        restore(repo / path, data, mode)
    return {
        "result": "pulled",
        "previous": head,
        "commit": target,
        "regenerate": regenerate,
        "kept_rendered": sorted(untracked),
    }


def rendered_removals(home, repo, head, target):
    removed = git(repo, "diff", "--name-only", "--diff-filter=D", "-z", head, target).split("\0")
    kept = {}
    for path in removed:
        file = repo / path
        if path and generated(home, path) and file.is_file() and not file.is_symlink():
            kept[path] = (file.read_bytes(), file.stat().st_mode & 0o7777)
    return kept


def restore(file, data, mode):
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_name(f".{file.name}.claude-config-pull")
    temporary.write_bytes(data)
    temporary.chmod(mode)
    os.replace(temporary, file)


def link_skills(home):
    linker = home / "repos/claude-config/scripts/link-skills"
    if not linker.is_file():
        return None
    try:
        proc = subprocess.run(
            [str(linker)],
            capture_output=True,
            text=True,
            timeout=120,
            env=dict(os.environ),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"result": "link_failed", "detail": str(exc)}
    summary = proc.stdout.strip() or proc.stderr.strip()
    if proc.returncode:
        return {"result": "link_failed", "detail": summary[-500:]}
    return {"result": "linked", "summary": summary}


def pull_claude_config(home):
    kept = []
    try:
        outcome = advance(home, home / ".claude")
        if outcome["result"] == "absent":
            linked = link_skills(home)
            if linked:
                outcome = linked
        else:
            kept = sorted(path for path in dirty_paths(home / ".claude") if not generated(home, path))
            if kept and outcome["result"] in ("current", "pulled"):
                outcome = dict(outcome, drift=kept)
    except Blocked as exc:
        outcome = {"result": exc.result, "detail": exc.detail}
    if outcome["result"] != "absent":
        try:
            notify(home, outcome, kept)
        except (OSError, subprocess.SubprocessError) as exc:
            outcome = dict(outcome, alert_failed=str(exc))
    return outcome
