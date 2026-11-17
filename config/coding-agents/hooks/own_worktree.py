import os
import subprocess

MOVES = {"cd", "pushd", "popd", "ssh", "chroot", "--chdir", "-C"}


def root(payload_cwd):
    start = os.environ.get("CLAUDE_PROJECT_DIR") or payload_cwd
    if not start or not os.path.isdir(start):
        return None
    try:
        done = subprocess.run(
            [
                "git",
                "-C",
                start,
                "rev-parse",
                "--path-format=absolute",
                "--show-toplevel",
                "--git-dir",
                "--git-common-dir",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = done.stdout.splitlines()
    if done.returncode or len(lines) != 3:
        return None
    top, git_dir, common = (os.path.realpath(line) for line in lines)
    return None if git_dir == common else top


def within(path, top, strict):
    return path.startswith(top + "/") or (not strict and path == top)


def forms(path):
    path = os.path.abspath(path)
    name = os.path.basename(path.rstrip("/"))
    parent = os.path.realpath(os.path.dirname(path.rstrip("/")) or "/")
    return {os.path.realpath(path), os.path.join(parent, name)}


def holds(top, cwd, word):
    if not top or not cwd or not word or "$" in word or "`" in word or word.startswith("~"):
        return False
    path = word if os.path.isabs(word) else os.path.join(cwd, word)
    if word == "*" or word.endswith("/*"):
        base = cwd if word == "*" else path[:-2] or "/"
        return within(os.path.realpath(base), top, strict=False)
    found = forms(path)
    git = os.path.join(top, ".git")
    return all(within(form, top, strict=True) and not within(form, git, strict=False) for form in found)


def moves(words):
    return any(word in MOVES or word.startswith("--chdir=") for word in words)
