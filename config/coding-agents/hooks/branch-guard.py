#!/usr/bin/env python3
import json
import os
import re
import shlex
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
try:
    from git_push_floor import (  # type: ignore[missing-import]
        ASSIGNMENT,
        BYPASS,
        COMMON_COMMANDS,
        DASHED,
        FORCE_PUSH,
        PUSH_VERBS,
        UNRESOLVED,
        judge,
        push_reason,
        split_globals,
    )
except ImportError as error:
    text = " ".join(sys.argv[3:]) if sys.argv[1:2] == ["--git-argv"] else sys.stdin.read()
    if sys.argv[1:2] == ["--git-argv"] or re.search(r"(?<![\w-])git(?![\w-])", text):
        print(
            f"git refused: the force-push floor cannot load git_push_floor.py ({error}); run coding-agents-sync.",
            file=sys.stderr,
        )
        sys.exit(2)
    sys.exit(0)

SHARED = (Path("/Volumes") / "StudioExt" / "repos", Path.home() / "repos")


@lru_cache(maxsize=128)
def first_checkout(cwd):
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                cwd,
                "rev-parse",
                "--path-format=absolute",
                "--show-toplevel",
                "--git-dir",
                "--git-common-dir",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        lines = result.stdout.splitlines()
        if result.returncode or len(lines) != 3:
            return None
        top, git_dir, common = (Path(line).resolve() for line in lines)
        roots = {path for root in SHARED for path in (root, root.resolve())}
        return git_dir == common and any(top.is_relative_to(root) for root in roots)
    except (OSError, subprocess.SubprocessError):
        return None


def removable_worktree(where, force=False):
    if not force:
        return True
    if not where:
        return False
    try:
        result = subprocess.run(
            ["git", "-C", where, "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return result.returncode == 0 and not result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False


def strip_heredocs(text):
    pending, kept = [], []
    marker = re.compile(r"<<(?P<tabs>-)?[ \t]*(?:'(?P<single>[^'\n]+)'|\"(?P<double>[^\"\n]+)\"|(?P<bare>[\w-]+))")
    quote = None
    for line in text.splitlines(keepends=True):
        comment = False
        if pending:
            delimiter, tabs = pending[0]
            if (line.lstrip("\t") if tabs else line).rstrip("\r\n") == delimiter:
                pending.pop(0)
            continue
        index, start = 0, 0
        while index < len(line):
            char = line[index]
            if char == "\\" and quote != "'":
                index += 2
                continue
            if quote:
                if char == quote:
                    quote = None
            elif char in "'\"":
                quote = char
            elif char == "#" and (index == 0 or line[index - 1].isspace()):
                comment = True
                break
            elif line.startswith("<<<", index):
                index += 3
                continue
            elif line.startswith("<<", index):
                match = marker.match(line, index)
                if match:
                    pending.append(
                        (
                            match.group("single") or match.group("double") or match.group("bare"),
                            bool(match.group("tabs")),
                        )
                    )
                    kept.append(line[start:index])
                    index = start = match.end()
                    continue
            index += 1
        kept.append(line[start:index] + ("\n" if line.endswith("\n") else "") if comment else line[start:])
    return "".join(kept)


def fallback_commands(text, cwd):
    pattern = re.compile(
        r"(?:^|[;\n|&])\s*(?:[^\s;|&]*/)?git\s+"
        r"(?:-C\s+(?P<target>'[^']*'|\"[^\"]*\"|[^\s;|&]+)\s+)?"
        r"(?P<action>switch|checkout|push|worktree\s+remove)\b(?P<args>[^\n;|&]*)"
    )
    found: list[tuple[str, str | None]] = []
    for match in pattern.finditer(text):
        action, args = match.group("action"), match.group("args").strip()
        target = match.group("target")
        if action == "push":
            if re.search(r"--force|--mir|(?:^|\s)-[a-zA-Z]*f|(?:^|\s)\+", args):
                found.append(("push denied: " + FORCE_PUSH, None))
            continue
        moved = re.search(r"(?:^|[;\n|&])\s*(?:cd|pushd|popd)\b", text[: match.start()])
        where = None if moved else str((Path(cwd) / target.strip("'\"")).resolve()) if target else cwd
        if action == "checkout" and (not args or args.startswith("-- ")):
            continue
        if action.startswith("worktree"):
            paths = re.findall(r"'[^']*'|\"[^\"]*\"|[^\s]+", args)
            paths = [part.strip("'\"") for part in paths if not part.startswith("-")]
            if not paths:
                continue
            where = (
                str(Path(paths[0]).resolve())
                if paths[0].startswith("/")
                else None
                if where is None
                else str((Path(where) / paths[0]).resolve())
            )
            force = bool(re.search(r"(?:^|\s)(?:--force|-[a-zA-Z]*f[a-zA-Z]*)(?:\s|$)", args))
            if not (where and any(char in where for char in "$`")) and removable_worktree(where, force):
                continue
            found.append((action, None))
            continue
        if where is None:
            found.append((action, None))
            continue
        found.append((action, None if any(char in where for char in "$`") else where))
    return found


WRAPPERS = {"env", "command", "exec", "sudo", "doas", "nohup", "time", "timeout", "nice", "setsid", "stdbuf"}
CONFIG_NAME = re.compile(r"(?:GIT_\w*|HOME|XDG_CONFIG_HOME)(?:=|$)")


def changes_git_env(segment):
    if segment[0] in {"export", "declare", "typeset", "local", "readonly", "unset"}:
        return any(CONFIG_NAME.match(word) for word in segment[1:])
    return all(ASSIGNMENT.fullmatch(word) for word in segment) and any(CONFIG_NAME.match(word) for word in segment)


def commands(text, cwd, depth=0, moved_env=False):
    if depth > 8:
        return [("unknown", None)]
    lexer = shlex.shlex(strip_heredocs(text).replace("\\\n", ""), posix=True, punctuation_chars=";&|()\n")
    lexer.whitespace_split = True
    lexer.whitespace = " \t\r"
    lexer.commenters = ""
    words = list(lexer)
    unsafe = any(word.strip("\n") in {"(", ")", "|", "&"} for word in words)
    segments, current = [], []
    for word in words + [";"]:
        if word and set(word) <= set(";&|()\n"):
            if current:
                segments.append(current)
                current = []
        else:
            current.append(word)
    found: list[tuple[str, str | None]] = []
    for segment in segments:
        if not segment:
            continue
        if changes_git_env(segment):
            moved_env = True
        if any(word.startswith(("GIT_DIR=", "GIT_WORK_TREE=")) for word in segment):
            unsafe = True
        dashed = next((i for i, word in enumerate(segment) if Path(word).name in DASHED), None)
        if dashed is not None:
            segment = [*segment[:dashed], "git", Path(segment[dashed]).name[4:], *segment[dashed + 1 :]]
        git_index = next((i for i, word in enumerate(segment) if Path(word).name == "git"), None)
        if git_index is not None:
            pre = segment[:git_index]
            prefix_head = next((word for word in pre if not ASSIGNMENT.fullmatch(word)), None)
            if prefix_head is not None and Path(prefix_head).name not in WRAPPERS:
                rest = segment[git_index + 1 :]
                verb = next((word for word in rest if word in PUSH_VERBS | {"receive-pack"}), None)
                if verb == "receive-pack" or verb and push_reason(rest[rest.index(verb) + 1 :], None, None):
                    found.append(("push denied: " + FORCE_PUSH, None))
                continue
            env = dict(os.environ)
            env.update(word.split("=", 1) for word in pre if ASSIGNMENT.fullmatch(word))
            names = {Path(word).name for word in pre}
            opaque = (
                moved_env
                or bool(names & {"sudo", "doas"})
                or "env" in names
                and any(word.startswith("-") for word in pre)
                or any(
                    any(c in word for c in "$`") and (not ASSIGNMENT.fullmatch(word) or CONFIG_NAME.match(word))
                    for word in pre
                )
            )
            args, where = segment[git_index + 1 :], cwd
            bypass = False
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
                        value = args.pop(0)
                        bypass |= arg in {"-c", "--config-env"} and value.lower().startswith("core.hookspath=")
                    if arg in {"--git-dir", "--work-tree"}:
                        where = None
                elif arg.startswith("-c"):
                    bypass |= arg[2:].lower().startswith("core.hookspath=")
                elif arg.startswith("--config-env="):
                    bypass |= arg.split("=", 1)[1].lower().startswith("core.hookspath=")
                elif arg.startswith(("--git-dir=", "--work-tree=")):
                    where = None
            if bypass:
                found.append(("hook bypass", None))
            if args and (args[0] in PUSH_VERBS | {"receive-pack"} or args[0] not in COMMON_COMMANDS):
                split = split_globals(segment[git_index + 1 :])
                reason = judge(*split, None if opaque else cwd, env) if split else UNRESOLVED
                if reason:
                    found.append(("push denied: " + reason, None))
            if args:
                action, rest = args[0], args[1:]
                if action == "commit":
                    takes_value = {
                        "-m",
                        "--message",
                        "-F",
                        "--file",
                        "-C",
                        "--reuse-message",
                        "-c",
                        "--reedit-message",
                        "--author",
                        "--date",
                        "--cleanup",
                        "--fixup",
                        "--squash",
                        "--trailer",
                        "-t",
                        "--template",
                    }
                    skip = False
                    for word in rest:
                        if skip:
                            skip = False
                            continue
                        if word == "--":
                            break
                        if (
                            word == "--no-verify"
                            or word.startswith("--no-verify=")
                            or re.fullmatch(r"-[a-zA-Z]*n[a-zA-Z]*", word)
                        ):
                            found.append(("hook bypass", None))
                            break
                        skip = word in takes_value
                flags = {"-b", "-B", "--orphan", "--detach"}
                if action == "switch" or action == "checkout" and rest and (flags & set(rest) or "--" not in rest):
                    found.append((action, where))
                elif action == "worktree" and rest[:1] == ["remove"]:
                    targets = [arg for arg in rest[1:] if not arg.startswith("-")]
                    if targets:
                        target = targets[0]
                        resolved = (
                            None
                            if any(x in target for x in "$`") or where is None and not target.startswith("/")
                            else str((Path(where or "/") / target).resolve())
                        )
                        force = any(arg == "--force" or re.fullmatch(r"-[a-zA-Z]*f[a-zA-Z]*", arg) for arg in rest[1:])
                        if not removable_worktree(resolved, force):
                            found.append(("worktree remove", None))
        name = Path(segment[0]).name
        if name in {"sh", "bash", "zsh"}:
            index = next((i for i, arg in enumerate(segment[1:], 1) if arg.startswith("-") and "c" in arg[1:]), None)
            if index is not None and index + 1 < len(segment):
                found.extend(commands(segment[index + 1], None if unsafe else cwd, depth + 1, moved_env))
        if name in {"cd", "pushd", "popd"}:
            targets = [word for word in segment[1:] if not word.startswith("-")]
            target = targets[0] if targets else str(Path.home())
            cwd = (
                None
                if unsafe or name == "popd" or not targets and len(segment) > 1 or any(x in target for x in "$`")
                else str((Path(cwd or "/") / target).resolve())
            )
    return found


def argv_main(args):
    found = commands(shlex.join(["git", *args]), os.getcwd())
    reasons = [
        action.removeprefix("push denied: ") if action != "push hook bypass" else BYPASS
        for action, _ in found
        if action.startswith("push denied: ") or action == "push hook bypass"
    ]
    if not reasons:
        return 0
    print(f"git push refused: {reasons[0]}", file=sys.stderr)
    return 2


def main():
    if sys.argv[1:2] == ["--git-argv"]:
        if sys.argv[2:3] == ["--"]:
            return argv_main(sys.argv[3:])
        print(
            "git push refused: branch-guard --git-argv expects `-- <git args>`; update the git shim.", file=sys.stderr
        )
        return 2
    try:
        payload = json.load(sys.stdin)
        tool = payload.get("tool_input") or {}
        text = tool.get("command", "")
        if not isinstance(text, str):
            raise ValueError("command must be text")
        cwd = payload.get("cwd") or os.getcwd()
        try:
            found = commands(text, cwd)
        except ValueError as error:
            print(
                f"branch-guard warning: cannot classify command: {error}; checking explicit git verbs", file=sys.stderr
            )
            found = fallback_commands(strip_heredocs(text), cwd)
        for action, where in found:
            state = first_checkout(where) if where else None
            if state is not False:
                reason = (
                    action.removeprefix("push denied: ")
                    if action.startswith("push denied: ")
                    else BYPASS
                    if action in {"hook bypass", "push hook bypass"}
                    else "git worktree remove --force would discard uncommitted files in that worktree; commit or move "
                    "them first, then remove it without --force."
                    if action == "worktree remove"
                    else "Floor: destructive shared-checkout mutation. Branch changes in the shared checkout can take "
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
                print(reason, file=sys.stderr)
                return 2
    except (OSError, ValueError, TypeError) as error:
        print(f"branch-guard: cannot classify command: {error}", file=sys.stderr)
    if os.environ.get("HOOK_FLOOR_RECEIPT"):
        print("floor-ok branch-guard.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
