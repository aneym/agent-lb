#!/usr/bin/env python3
import json
import os
import posixpath
import re
import sys

ROOTS = {
    "/",
    "/*",
    "~",
    "~/*",
    "$HOME",
    "${HOME}",
    "$HOME/*",
    "${HOME}/*",
    "/home",
    "/root",
    "/Users",
    "/Volumes",
    "/etc",
    "/usr",
    "/var",
    "/opt",
    "/bin",
    "/sbin",
    "/lib",
    "/System",
    "/Library",
    "/Applications",
    ".",
    "..",
    "*",
}


def protected(target):
    candidates = [target]
    if target == "$HOME" or target.startswith("$HOME/"):
        candidates.append(os.path.expanduser("~") + target[5:])
    elif target == "~" or target.startswith("~/"):
        candidates.append(os.path.expanduser("~") + target[1:])
    for candidate in candidates:
        candidate = posixpath.normpath(re.sub(r"/+", "/", candidate))
        if candidate.endswith("/*"):
            candidate = candidate[:-2] or "/"
        if (
            candidate in ROOTS
            or re.fullmatch(r"/(?:home|Users|Volumes)/[^/]+", candidate) is not None
            or re.fullmatch(r"~[^/]+", candidate) is not None
        ):
            return True
    return False


def unsafe_invocation(args):
    recursive = force = False
    options = True
    targets = []
    for arg in args:
        if options and arg == "--":
            options = False
        elif options and arg.startswith("--"):
            recursive |= len(arg) >= len("--r") and "--recursive".startswith(arg)
            force |= len(arg) >= len("--f") and "--force".startswith(arg)
        elif options and arg.startswith("-"):
            recursive |= any(flag in arg[1:] for flag in "rR")
            force |= "f" in arg[1:]
        else:
            targets.append(arg)
    return recursive and force and any(protected(target) for target in targets)


def dangerous(command):
    try:
        return parse_dangerous(command)
    except Exception:
        return (
            re.search(
                r"(?<!\w)(?:\S*/)?rm\s+"
                r"(?=[^;\n]*-[A-Za-z]*[rR])(?=[^;\n]*-[A-Za-z]*f)"
                r"(?:-[A-Za-z]+\s+)+"
                r"(?:/(?:\*|(?:home|Users|Volumes)(?:/[^/\s;]+)?|root|etc|usr|var|opt|"
                r"bin|sbin|lib|System|Library|Applications)?/?|~[^/\s;]*(?:/\*)?|"
                r"\$HOME(?:/\*|/\.)?|\$\{HOME\}(?:/\*)?|\.\.?|\*)"
                r'(?=\s|[;\'"`]|$)',
                command,
            )
            is not None
        )


def parse_dangerous(command):
    if re.search(r"\bDROP\s+(DATABASE|TABLE)", command, re.I):
        return True
    command = re.sub(r"\$\{(HOME|USER)[^}]*\}", r"$\1", command)
    command = re.sub(
        r"(?:(?<!\S)\d+)?(?:&>>|&>|<<<|<<|>>|[<>]&|>\||[<>])"
        r'[ \t]*(?:"[^"\n]*"|\'[^\'\n]*\'|\\[^\n]|[^\s;&|<>()\x60\'"\\])+',
        "",
        command,
    )
    flattened = re.sub(r"[;&|(){}\x60\n]", " ; ", command)
    tokens = flattened.replace("'", " ").replace('"', " ").split()
    for i, token in enumerate(tokens):
        token = token.lstrip("\\")
        if token == "rm" or token.endswith("/rm"):
            end = i + 1
            while end < len(tokens) and tokens[end] != ";":
                end += 1
            if unsafe_invocation(tokens[i + 1 : end]):
                return True
    return False


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    if not isinstance(payload, dict) or not isinstance(payload.get("tool_input"), dict):
        return 0
    command = payload["tool_input"].get("command")
    if isinstance(command, str) and dangerous(command):
        print("BLOCKED: Dangerous command requires explicit user approval.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
