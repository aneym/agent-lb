#!/usr/bin/env python3

import fnmatch
import json
import os
import shlex
import sys

FLOOR = "Alex's credentials"
RECEIPT = "floor-ok host-credential-guard.py"
NAME = "host.credential"
SAFE_HEADS = {"ls", "stat", "cd", "pushd"}
SPLIT = {";", "&&", "||", "|", "&", "|&", ";;", "(", ")", "\n"}
REDIRECT = {"<", ">", ">>", "<>", "<<<", ">|", "&>", "&>>"}
CODE = set(" '\"()+,;%{}")


def refuse(why):
    sys.stderr.write(
        'BLOCKED: kept floor "%s": host-credential-guard refuses %s. Reading or copying '
        "~/.agent-rails/rails-host/host.credential is never allowed; ls and stat of the directory are.\n" % (FLOOR, why)
    )
    sys.exit(2)


def targets():
    home = os.environ.get("HOME") or os.path.expanduser("~")
    folder = os.path.join(home, ".agent-rails", "rails-host")
    paths = {os.path.join(folder, NAME), os.path.realpath(os.path.join(folder, NAME))}
    folders = {folder, os.path.realpath(folder)}
    return home, paths, folders


def expand(word, home):
    for alias in ("${HOME}", "$HOME"):
        word = word.replace(alias, home)
    if word == "~" or word.startswith("~/"):
        word = home + word[1:]
    return word


def touches(word, base, home, paths, folders):
    word = expand(word, home)
    full = os.path.normpath(os.path.join(base, word))
    return full in paths


def segments(command):
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    current = []
    for token in lexer:
        if token in SPLIT:
            yield current
            current = []
        else:
            current.append(token)
    yield current


def check_bash(command, cwd):
    home, paths, folders = targets()
    base = cwd if isinstance(cwd, str) and cwd else os.getcwd()
    try:
        parts = list(segments(command))
    except ValueError:
        if any(path in expand(command, home) for path in paths):
            refuse("a shell command it cannot parse that names the credential")
        return
    for words in parts:
        heads = [w for w in words if not (w.split("=", 1)[0].isidentifier() and "=" in w)]
        head = os.path.basename(heads[0]) if heads else ""
        values = [w.split("=", 1)[1] if w not in heads else w for w in words]
        hits = [i for i, w in enumerate(values) if touches(w, base, home, paths, folders)]
        if hits and head not in SAFE_HEADS:
            refuse("`%s` on the host credential" % (head or "an assignment"))
        for i in hits:
            if "$(" in words[i] or "`" in words[i] or (i and words[i - 1] in REDIRECT):
                refuse("a substitution or redirect on the host credential")
        if head in ("cd", "pushd") and len(heads) > 1 and "$" not in heads[1]:
            base = os.path.normpath(os.path.join(base, expand(heads[1], home)))


def check_read(path):
    home, paths, folders = targets()
    if not isinstance(path, str):
        refuse("a Read call whose file_path is not text")
    full = os.path.normpath(expand(path, home))
    if full in paths:
        refuse("a Read of the host credential")


def main():
    try:
        data = json.loads(sys.stdin.read())
    except ValueError as error:
        refuse("hook input that is not JSON (%s), so it fails closed" % error)
    if not isinstance(data, dict):
        refuse("hook input that is not a JSON object, so it fails closed")
    tool = data.get("tool_name")
    ti = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    if tool == "Read":
        check_read(ti.get("file_path"))
    elif tool == "Bash":
        command = ti.get("command")
        if isinstance(command, str):
            check_bash(command, data.get("cwd"))


if __name__ == "__main__":
    main()
    if os.environ.get("HOOK_FLOOR_RECEIPT"):
        print(RECEIPT)
