#!/usr/bin/env python3
import json
import os
import re
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
            return None
        first = next((line[9:] for line in result.stdout.splitlines() if line.startswith("worktree ")), "")
        if not first:
            return None
        path = Path(first).resolve()
        return (
            path.is_relative_to(ROOT)
            and Path(cwd).resolve().is_relative_to(path)
            and not (Path(cwd).resolve() != path and ".git" in Path(cwd).parts)
        )
    except (OSError, subprocess.SubprocessError):
        return None


def strip_heredocs(text):
    pending, kept = [], []
    marker = re.compile(r"<<(?P<tabs>-)?[ \t]*(?:'(?P<single>[^'\n]+)'|\"(?P<double>[^\"\n]+)\"|(?P<bare>[\w-]+))")
    quote = None
    for line in text.splitlines(keepends=True):
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
        kept.append(line[start:])
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
            if re.search(r"--force|(?:^|\s)-[a-zA-Z]*f|(?:^|\s)\+", args):
                found.append(
                    ("push denied: Cannot safely classify force push; use an explicit owned feature branch.", None)
                )
            continue
        if re.search(r"(?:^|[;\n|&])\s*(?:cd|pushd|popd)\b", text[: match.start()]):
            found.append((action, None))
            continue
        where = str((Path(cwd) / target.strip("'\"")).resolve()) if target else cwd
        if action == "checkout" and (not args or args.startswith("-- ")):
            continue
        if action.startswith("worktree"):
            paths = re.findall(r"'[^']*'|\"[^\"]*\"|[^\s]+", args)
            paths = [part.strip("'\"") for part in paths if not part.startswith("-")]
            if not paths:
                continue
            where = str((Path(where) / paths[0]).resolve())
        found.append((action, None if any(char in where for char in "$`") else where))
    return found


def git_output(cwd, *args):
    if not cwd:
        return ""
    try:
        result = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=3, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def push_reason(args, cwd):
    plain, lease, broad, operands, expects = False, False, False, [], []
    options, skip = True, False
    for arg in args:
        if skip:
            skip = False
            continue
        if options and arg == "--":
            options = False
        elif options and arg.startswith("-"):
            plain |= arg == "--force" or bool(re.fullmatch(r"-[a-zA-Z]*f[a-zA-Z]*", arg))
            broad |= arg in {"--all", "--mirror", "--branches", "--tags"}
            if arg == "--force-with-lease" or arg.startswith("--force-with-lease="):
                lease = True
                if "=" in arg:
                    expects.append(arg.split("=", 1)[1].split(":", 1)[0])
            skip = arg in {"--repo", "--receive-pack", "--exec", "--push-option", "-o"}
            if arg.startswith("--repo="):
                operands.insert(0, arg.split("=", 1)[1])
            elif arg == "--repo":
                operands.insert(0, "configured-remote")
        else:
            operands.append(arg)
    specs = operands[1:]
    plain |= any(spec.startswith("+") for spec in specs)
    if plain:
        return "Plain force pushes (--force, -f, +refspec) can lose work; use --force-with-lease on your own feature branch."
    if not lease and "--mirror" not in args:
        return None
    if broad or not cwd:
        return "Cannot establish one owned feature branch for force push; specify your own non-default branch."
    current = git_output(cwd, "symbolic-ref", "--quiet", "--short", "HEAD")
    remote = operands[0] if operands else git_output(cwd, "config", f"branch.{current}.remote") or "origin"
    default = git_output(cwd, "symbolic-ref", "--quiet", "--short", f"refs/remotes/{remote}/HEAD")
    defaults = {"main", "master", git_output(cwd, "config", "init.defaultBranch")}
    if default:
        defaults.add(default.removeprefix(f"{remote}/"))
    checkout = git_output(cwd, "worktree", "list", "--porcelain").split("\n\n", 1)[0]
    defaults.update(
        line.removeprefix("branch refs/heads/")
        for line in checkout.splitlines()
        if line.startswith("branch refs/heads/")
    )
    if not specs:
        specs = git_output(cwd, "config", "--get-all", f"remote.{remote}.push").splitlines()
    if not specs:
        mode = git_output(cwd, "config", "push.default") or "simple"
        upstream = git_output(cwd, "config", f"branch.{current}.merge")
        if mode in {"matching", "nothing"}:
            return "Force push has no explicit owned feature branch; specify its refspec."
        specs = [upstream if mode in {"simple", "upstream"} and upstream else current]
    destinations = []
    for spec in specs + expects:
        destination = spec.split(":", 1)[-1].removeprefix("refs/heads/")
        if destination in {"HEAD", "@"}:
            destination = current
        if not destination or any(char in destination for char in "*$`?") or destination.startswith("refs/"):
            return "Force push destination is not a known owned feature branch."
        if destination in defaults:
            return f"Force pushes to default branch {destination} are forbidden, including --force-with-lease."
        destinations.append(destination)
    worktrees = git_output(cwd, "worktree", "list", "--porcelain")
    owners = {}
    for record in worktrees.split("\n\n"):
        path = next((line[9:] for line in record.splitlines() if line.startswith("worktree ")), "")
        branch = next((line[18:] for line in record.splitlines() if line.startswith("branch refs/heads/")), "")
        if branch:
            owners[branch] = path
    for branch in destinations:
        owner = owners.get(branch)
        if owner and not Path(cwd).resolve().is_relative_to(Path(owner).resolve()):
            return f"Force push to {branch} is forbidden: another lane's worktree owns this ref."
        if branch != current and git_output(cwd, "show-ref", "--verify", f"refs/heads/{branch}"):
            return f"Force push to {branch} is forbidden: this is not your worktree's branch."
    print(
        f"branch-guard warning: --force-with-lease updates feature branch {', '.join(dict.fromkeys(destinations))}",
        file=sys.stderr,
    )
    return None


def commands(text, cwd, depth=0):
    if depth > 8:
        return [("unknown", None)]
    lexer = shlex.shlex(strip_heredocs(text), posix=True, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    words = list(lexer)
    unsafe = any(word in {"(", ")", "|", "&"} for word in words)
    segments, current = [], []
    for word in words + [";"]:
        if word and set(word) <= set(";&|()"):
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
            }:
                continue
            args, where = segment[git_index + 1 :], cwd
            bypass = False
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
            if args:
                action, rest = args[0], args[1:]
                if action == "push":
                    reason = push_reason(rest, where)
                    if reason:
                        found.append(("push denied: " + reason, None))
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
                        found.append(
                            (
                                "worktree remove",
                                None
                                if where is None or any(x in target for x in "$`")
                                else str((Path(where) / target).resolve()),
                            )
                        )
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
                    else "Floor: destructive shared-checkout mutation. Branch changes or removal of the shared checkout can take "
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
                return 2
    except (OSError, ValueError, TypeError) as error:
        print(f"branch-guard: cannot classify command: {error}", file=sys.stderr)
    if os.environ.get("HOOK_FLOOR_RECEIPT"):
        print("floor-ok branch-guard.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
