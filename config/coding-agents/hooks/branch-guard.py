#!/usr/bin/env python3
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path("/Volumes") / "StudioExt" / "repos"


def first_checkout(cwd):
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode:
            return False
        first = next((line[9:] for line in result.stdout.splitlines() if line.startswith("worktree ")), "")
        path = Path(first).resolve()
        return (
            path.is_relative_to(ROOT)
            and Path(cwd).resolve().is_relative_to(path)
            and not (Path(cwd).resolve() != path and ".git" in Path(cwd).parts)
        )
    except (OSError, subprocess.SubprocessError):
        return None


def commands(text, cwd, depth=0):
    if depth > 8:
        return [("unknown", None)]
    lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    words = list(lexer)
    unsafe = any(word in {"(", ")", "|", "||", "&&", "&"} for word in words)
    segments, current = [], []
    for word in words + [";"]:
        if word and set(word) <= set(";&|()"):
            if current:
                segments.append(current)
                current = []
        else:
            current.append(word)
    found = []
    for segment in segments:
        if not segment:
            continue
        if any(word.startswith(("GIT_DIR=", "GIT_WORK_TREE=")) for word in segment):
            unsafe = True
        git_index = next((i for i, word in enumerate(segment) if Path(word).name == "git"), None)
        if git_index is not None:
            args, where = segment[git_index + 1 :], cwd
            pre = segment[:git_index]
            if any(word in {"-C", "--chdir", "-D"} or word.startswith("--chdir=") for word in pre):
                where = None
            if unsafe:
                where = None
            while args and args[0].startswith("-"):
                arg = args.pop(0)
                if arg == "-C" and args:
                    target = args.pop(0)
                    where = (
                        None
                        if where is None or any(x in target for x in "$`")
                        else str((Path(where) / target).resolve())
                    )
                elif arg.startswith("-C"):
                    where = None if where is None else str((Path(where) / arg[2:]).resolve())
                elif arg in {"-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}:
                    if args:
                        args.pop(0)
                    if arg in {"--git-dir", "--work-tree"}:
                        where = None
                elif arg.startswith(("--git-dir=", "--work-tree=")):
                    where = None
            if args:
                action, rest = args[0], args[1:]
                flags = {"-b", "-B", "--orphan", "--detach"}
                if action == "switch" or action == "checkout" and rest and (flags & set(rest) or "--" not in rest):
                    found.append((action, where))
        name = Path(segment[0]).name
        if name in {"sh", "bash", "zsh"}:
            index = next((i for i, arg in enumerate(segment[1:], 1) if arg.startswith("-") and "c" in arg[1:]), None)
            if index is not None and index + 1 < len(segment):
                found.extend(commands(segment[index + 1], None if unsafe else cwd, depth + 1))
        if name in {"cd", "pushd", "popd"}:
            targets = [word for word in segment[1:] if not word.startswith("-")]
            target = targets[0] if targets else str(Path.home())
            cwd = (
                None
                if unsafe or name == "popd" or not targets and len(segment) > 1 or any(x in target for x in "$`")
                else str((Path(cwd or "/") / target).resolve())
            )
    return found


def main():
    try:
        payload = json.load(sys.stdin)
        tool = payload.get("tool_input") or {}
        text = tool.get("command", "")
        cwd = payload.get("cwd") or os.getcwd()
        for action, where in commands(text, cwd):
            state = first_checkout(where) if where else None
            if state is not False:
                reason = (
                    "Floor: destructive shared-checkout mutation. Branch changes in the main checkout can take "
                    "another agent's working state; use a dedicated <repo>-wt/<topic> worktree."
                )
                print(
                    json.dumps(
                        {
                            "hookSpecificOutput": {
                                "hookEventName": "PreToolUse",
                                "permissionDecision": "deny",
                                "permissionDecisionReason": reason,
                            }
                        }
                    )
                )
                return 0
    except (OSError, ValueError, TypeError) as error:
        print(f"branch-guard: cannot classify command: {error}", file=sys.stderr)
        return 2
    if os.environ.get("HOOK_FLOOR_RECEIPT"):
        print("floor-ok branch-guard.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
