#!/usr/bin/env python3
import json
import os
import re
import shlex
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

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


FORCE_PUSH = (
    "Force pushes (--force, -f, --force-with-lease, --force-if-includes, --mirror, +refspec) are a kept floor; "
    "push rebased work to a new branch instead."
)


FORCE_OPTIONS = ("force", "mirror", "force-with-lease", "force-if-includes")
COMMON_COMMANDS = frozenset(
    "add am apply bisect blame branch cat-file checkout cherry-pick clean clone commit config describe diff fetch "
    "for-each-ref format-patch gc grep init log ls-files ls-remote ls-tree merge merge-base mv notes pull push rebase "
    "reflog remote reset restore rev-list rev-parse revert rm show show-ref stash status submodule switch symbolic-ref "
    "tag update-index update-ref worktree".split()
)
TRUE = {"", "true", "yes", "on", "1"}


@lru_cache(maxsize=64)
def config_items(where, inline):
    items = [(value.split("=", 1) + [""])[:2] for value in inline]
    if where:
        flags = [part for value in inline for part in ("-c", value)]
        try:
            result = subprocess.run(
                ["git", *flags, "-C", where, "config", "--get-regexp", r"^(remote\..*\.(push|mirror)|alias\..*)$"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode in {0, 1}:
                items = [(line.split(" ", 1) + [""])[:2] for line in result.stdout.splitlines()]
        except (OSError, subprocess.SubprocessError):
            pass
    return tuple((key, value) for key, value in items)


def push_reason(args, items=()):
    force, options, skip, operands, remote = False, True, None, [], None
    for arg in args:
        if skip:
            remote = arg if skip == "--repo" else remote
            skip = None
            continue
        if options and arg == "--":
            options = False
        elif options and arg.startswith("--"):
            name = arg[2:].split("=", 1)[0]
            force |= any(option.startswith(name) for option in FORCE_OPTIONS)
            remote = arg.split("=", 1)[1] if arg.startswith("--repo=") else remote
            skip = arg if arg in {"--repo", "--receive-pack", "--exec", "--push-option"} else None
        elif options and arg.startswith("-") and arg != "-":
            force |= "f" in arg[1:].split("o", 1)[0]
            skip = "-o" if arg[1:].split("o", 1)[1:] == [""] else None
        else:
            operands.append(arg)
    force |= any(operand.startswith("+") for operand in operands)
    remote = operands[0] if operands else remote
    for key, value in items:
        lowered = key.lower()
        if not lowered.startswith("remote.") or remote is not None and key[7:].rsplit(".", 1)[0] != remote:
            continue
        force |= lowered.endswith(".mirror") and value.strip().lower() in TRUE
        force |= lowered.endswith(".push") and len(operands) < 2 and value.strip().startswith("+")
    return FORCE_PUSH if force else None


def alias_text(action, items):
    if action in COMMON_COMMANDS:
        return None
    return next((value for key, value in reversed(items) if key.lower() == f"alias.{action.lower()}"), None)


def commands(text, cwd, depth=0):
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
        if any(word.startswith(("GIT_DIR=", "GIT_WORK_TREE=")) for word in segment):
            unsafe = True
        git_index = next((i for i, word in enumerate(segment) if Path(word).name == "git"), None)
        if git_index is not None:
            prefix_head = next((word for word in segment[:git_index] if "=" not in word), None)
            if prefix_head is not None and Path(prefix_head).name not in {
                "env",
                "command",
                "exec",
                "sudo",
                "nohup",
                "time",
                "timeout",
            }:
                rest = segment[git_index + 1 :]
                if "push" in rest and push_reason(rest[rest.index("push") + 1 :]):
                    found.append(("push denied: " + FORCE_PUSH, None))
                continue
            args, where = segment[git_index + 1 :], cwd
            bypass, inline = False, []
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
                        value = args.pop(0)
                        bypass |= arg in {"-c", "--config-env"} and value.lower().startswith("core.hookspath=")
                        inline += [value] if arg == "-c" else []
                    if arg in {"--git-dir", "--work-tree"}:
                        where = None
                elif arg.startswith("-c"):
                    bypass |= arg[2:].lower().startswith("core.hookspath=")
                    inline.append(arg[2:])
                elif arg.startswith("--config-env="):
                    bypass |= arg.split("=", 1)[1].lower().startswith("core.hookspath=")
                elif arg.startswith(("--git-dir=", "--work-tree=")):
                    where = None
            if bypass:
                found.append(("hook bypass", None))
            items = (
                config_items(where or cwd, tuple(inline)) if args and args[0] not in COMMON_COMMANDS - {"push"} else ()
            )
            alias = alias_text(args[0], items) if args else None
            if alias is not None:
                head = (
                    alias[1:]
                    if alias.startswith("!")
                    else shlex.join([segment[git_index], *[f"-c{v}" for v in inline]]) + " " + alias
                )
                found.extend(commands(head + " " + shlex.join(args[1:]), where or cwd, depth + 1))
                args = []
            if args:
                action, rest = args[0], args[1:]
                if action == "push":
                    reason = push_reason(rest, items)
                    if reason:
                        found.append(("push denied: " + reason, None))
                    if "--no-verify" in rest:
                        found.append(("hook bypass", None))
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


TRUSTED_CALLERS = (".agent-rails/factory-runtime.releases", ".agent-rails/factory-runtime", ".agent-rails/overlay")


def caller_script(pid):
    try:
        argv = [part.decode() for part in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[:-1]]
        base = os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        ps = subprocess.run(
            ["ps", "-o", "command=", "-p", pid], capture_output=True, text=True, timeout=10, check=False
        )
        argv, base = ps.stdout.split(), "/"
    if not argv or not Path(argv[0]).name.startswith("python"):
        return None
    skip = False
    for arg in argv[1:]:
        if skip or arg in {"-X", "-W"}:
            skip = not skip
        elif arg in {"-c", "-m", "-"}:
            return None
        elif not arg.startswith("-"):
            return Path(os.path.realpath(Path(base) / arg))
    return None


def argv_main(pid, args):
    found = commands(shlex.join(["git", *args]), os.getcwd())
    reasons = [action.removeprefix("push denied: ") for action, _ in found if action.startswith("push denied: ")]
    script = caller_script(pid) if reasons else None
    roots = [Path(os.path.realpath(Path.home() / root)) for root in TRUSTED_CALLERS]
    if not reasons or script and any(script.is_relative_to(root) for root in roots):
        return 0
    print(f"git push refused: {reasons[0]}", file=sys.stderr)
    return 2


def main():
    if sys.argv[1:2] == ["--git-argv"] and sys.argv[3:4] == ["--"]:
        return argv_main(sys.argv[2], sys.argv[4:])
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
                    else "Git hook bypass is forbidden: do not use --no-verify, -n or core.hooksPath overrides; fix the hook failure."
                    if action == "hook bypass"
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
