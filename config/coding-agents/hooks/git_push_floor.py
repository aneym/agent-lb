import os
import re
import shlex
import subprocess

BYPASS = "Git hook bypass is forbidden: do not use --no-verify, -n or core.hooksPath overrides; fix the hook failure."
FORCE_PUSH = (
    "Force pushes (--force, -f, --force-with-lease, --force-if-includes, --mirror, +refspec) are a kept floor; "
    "push rebased work to a new branch instead."
)
CONFIG_ENV = "git --config-env on a push or alias hides its value; use -c."
RECEIVE = "git receive-pack skips the force check; use git push."
SHELL_ALIAS = "A git alias that runs a shell command (!) hides what it pushes; run the git command itself."
UNRESOLVED = (
    "The force-push floor cannot resolve which repository and config this git command would use; "
    "run it from inside the repository, without variables in its git options or environment."
)
PUSH_VERBS = {"push", "send-pack", "http-push"}
DASHED = {"git-send-pack", "git-receive-pack", "git-http-push"}
FORCE_OPTIONS = ("force", "mirror", "force-with-lease", "force-if-includes")
COMMON_COMMANDS = frozenset(
    "add am apply bisect blame branch cat-file checkout cherry-pick clean clone commit config describe diff fetch "
    "for-each-ref format-patch gc grep init log ls-files ls-remote ls-tree merge merge-base mv notes pull push rebase "
    "reflog remote reset restore rev-list rev-parse revert rm show show-ref stash status submodule switch symbolic-ref "
    "tag update-index update-ref worktree".split()
)
VALUE_GLOBALS = {
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--config-env",
    "--exec-path",
    "--super-prefix",
    "--shallow-file",
    "--attr-source",
}
FLAG_GLOBALS = {
    "--bare",
    "-p",
    "--paginate",
    "-P",
    "--no-pager",
    "--no-replace-objects",
    "--no-lazy-fetch",
    "--no-optional-locks",
    "--no-advice",
    "--literal-pathspecs",
    "--glob-pathspecs",
    "--noglob-pathspecs",
    "--icase-pathspecs",
}
CONFIG_KEYS = (
    r"^(remote\..*\.(push|mirror)|push\.default|remote\.pushdefault|branch\..*\.(pushremote|remote)|alias\..*)$"
)
PUSH_DEFAULTS = {"nothing", "current", "upstream", "tracking", "simple", "matching"}
FALSE = {"false", "no", "off", "0"}
ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=.*", re.S)


def split_globals(args):
    found, rest = [], list(args)
    while rest and rest[0].startswith("-"):
        arg = rest.pop(0)
        name = arg.split("=", 1)[0]
        if arg in VALUE_GLOBALS:
            if not rest:
                return None
            found += [arg, rest.pop(0)]
        elif name in VALUE_GLOBALS and "=" in arg or arg[:2] in {"-C", "-c"} and len(arg) > 2:
            found.append(arg)
        elif arg in FLAG_GLOBALS:
            found.append(arg)
        else:
            return None
    return found, rest


def git(options, cwd, env, *args):
    try:
        result = subprocess.run(
            ["git", *options, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result


def resolve(options, cwd, env, need_repo):
    if cwd is None or not os.path.isdir(cwd) or any(c in part for part in options for c in "$`"):
        return None
    branch = None
    if need_repo:
        repo = git(options, cwd, env, "rev-parse", "--git-dir")
        if repo is None or repo.returncode:
            return None
        head = git(options, cwd, env, "symbolic-ref", "-q", "--short", "HEAD")
        if head is None or head.returncode not in {0, 1}:
            return None
        branch = head.stdout.strip() or None
    listed = git(options, cwd, env, "config", "--get-regexp", CONFIG_KEYS)
    if listed is None or listed.returncode not in {0, 1}:
        return None
    items = [tuple((line.split(" ", 1) + [""])[:2]) for line in listed.stdout.splitlines()]
    return items, branch


def values(items, key):
    return [value for name, value in items if name.lower() == key.lower()]


def push_reason(args, items, branch):
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
            if len(arg) > 5 and "--no-verify".startswith(arg):
                return BYPASS
            force |= any(option.startswith(name) for option in FORCE_OPTIONS)
            remote = arg.split("=", 1)[1] if arg.startswith("--repo=") else remote
            skip = arg if arg in {"--repo", "--receive-pack", "--exec", "--push-option"} else None
        elif options and arg.startswith("-") and arg != "-":
            force |= "f" in arg[1:].split("o", 1)[0]
            skip = "-o" if arg[1:].split("o", 1)[1:] == [""] else None
        else:
            operands.append(arg)
    force |= any(operand.startswith("+") for operand in operands)
    if items is None:
        return FORCE_PUSH if force else None
    remote = operands[0] if operands else remote
    if remote is None:
        chosen = values(items, f"branch.{branch}.pushremote") if branch else []
        chosen = chosen or values(items, "remote.pushdefault")
        chosen = chosen or (values(items, f"branch.{branch}.remote") if branch else [])
        remote = chosen[-1] if chosen else "origin"
    mirror = values(items, f"remote.{remote}.mirror")
    force |= bool(mirror) and mirror[-1].strip().lower() not in FALSE
    refspecs = values(items, f"remote.{remote}.push")
    if len(operands) < 2:
        force |= any(value.strip().startswith("+") for value in refspecs)
        default = values(items, "push.default")
        if default and default[-1].strip().lower() not in PUSH_DEFAULTS:
            return UNRESOLVED
    return FORCE_PUSH if force else None


def judge(options, args, cwd, env, depth=0):
    if not args:
        return None
    verb, rest = args[0], args[1:]
    if verb == "receive-pack":
        return RECEIVE
    if verb in COMMON_COMMANDS - {"push"}:
        return None
    if depth > 8:
        return UNRESOLVED
    if any(part.startswith("--config-env") for part in options):
        return CONFIG_ENV
    if verb in PUSH_VERBS:
        resolved = resolve(options, cwd, env, True)
        if resolved is None:
            return UNRESOLVED
        items, branch = resolved
        return push_reason(rest, items if verb == "push" else None, branch)
    resolved = resolve(options, cwd, env, False)
    if resolved is None:
        return UNRESOLVED
    alias = values(resolved[0], f"alias.{verb}")
    if not alias:
        return None
    if alias[-1].lstrip().startswith("!"):
        return SHELL_ALIAS
    try:
        split = split_globals(shlex.split(alias[-1]))
    except ValueError:
        return UNRESOLVED
    if split is None:
        return UNRESOLVED
    more, words = split
    return judge(options + more, words + rest, cwd, env, depth + 1)
