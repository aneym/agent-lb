import os
import re

from psql_commands import restore  # type: ignore[missing-import]

VALUED = set("cdfFhLoPpRTUv")
LONG = {
    "--command": "c",
    "--dbname": "d",
    "--file": "f",
    "--list": "l",
    "--variable": "v",
    "--set": "v",
    "--version": "V",
    "--no-psqlrc": "",
    "--single-transaction": "",
    "--help": "?",
    "--echo-all": "",
    "--echo-errors": "",
    "--echo-queries": "",
    "--echo-hidden": "",
    "--log-file": "L",
    "--no-readline": "",
    "--output": "o",
    "--quiet": "",
    "--single-step": "",
    "--single-line": "",
    "--no-align": "",
    "--csv": "",
    "--field-separator": "F",
    "--html": "",
    "--pset": "P",
    "--record-separator": "R",
    "--tuples-only": "",
    "--table-attr": "T",
    "--expanded": "",
    "--field-separator-zero": "",
    "--record-separator-zero": "",
    "--host": "h",
    "--port": "p",
    "--username": "U",
    "--no-password": "",
    "--password": "",
}
STDIN_PATHS = {"-", "/dev/stdin", "/dev/fd/0", "/proc/self/fd/0"}
IN_REDIRECTS = {"<", "<>", "<<", "<<<", "<&"}
OUT_REDIRECTS = {">", ">>", ">|", "&>", "&>>", ">&"}
REDIRECTS = IN_REDIRECTS | OUT_REDIRECTS
EXPANSION = re.compile(r"(?<!\\)\$[({A-Za-z_0-9@*#?$!-]|(?<!\\)`|\x04")
READ_ONLY = {
    "cd",
    "pushd",
    "popd",
    "export",
    "unset",
    "set",
    "true",
    "false",
    ":",
    "test",
    "[",
    "echo",
    "printf",
    "sleep",
    "pwd",
    "ls",
    "cat",
    "less",
    "head",
    "tail",
    "grep",
    "rg",
    "wc",
    "stat",
    "file",
    "diff",
    "cmp",
    "sha256sum",
    "shasum",
    "md5sum",
    "which",
    "type",
    "date",
    "whoami",
    "hostname",
}
NAMES_PSQL = READ_ONLY | {"command", "man", "whereis", "ps", "pgrep", "pkill", "killall", "apt", "apt-get", "brew"}
FANS_OUT = {"xargs", "parallel", "find", "fd", "watch"}
FEEDS = "psql would read SQL the guard cannot see"


def unseen(source):
    return not source or source.startswith(("/dev/", "/proc/", "<", ">")) or bool(EXPANSION.search(source))


def option(word):
    key, eq, rest = word.partition("=")
    names = [name for name in LONG if name.startswith(key)]
    name = key if key in LONG else names[0] if len(names) == 1 else None
    return (LONG[name], eq, rest) if name else ("", eq, rest)


def flags_of(word, args, i):
    if word[:2] == "--":
        flag, eq, value = option(word)
        if not eq and flag and flag in VALUED:
            value, i = (args[i] if i < len(args) else ""), i + 1
        return [(flag, value)], i
    found = []
    for j, flag in enumerate(word[1:], 1):
        if flag in VALUED:
            value = word[j + 1 :]
            if not value and i < len(args):
                value, i = args[i], i + 1
            found.append((flag, value))
            break
        found.append((flag, ""))
    return found, i


def inspect(args, read, inline, unread, heredoc):
    found, writes, source, sql, info, stdin = [], [], None, False, False, False
    i, fd = 0, None
    while i < len(args):
        word, i = args[i], i + 1
        if word.isdigit() and i < len(args) and args[i] in REDIRECTS:
            fd = int(word)
            continue
        if word in REDIRECTS:
            target, i = (args[i] if i < len(args) else ""), i + 1
            if word in OUT_REDIRECTS or word == "<>" and fd not in (None, 0):
                writes.append(restore(target))
            elif fd in (None, 0):
                source = (word, target)
            fd = None
            continue
        fd = None
        if word[:1] != "-" or len(word) < 2:
            continue
        flags, i = flags_of(word, args, i)
        for flag, value in flags:
            info |= flag in ("l", "V", "?")
            if flag in ("o", "L"):
                writes.append(restore(value))
            elif flag == "v":
                found.append(restore(value))
            elif flag == "c":
                sql = True
                if EXPANSION.search(value):
                    unread.append("-c text with shell expansion")
                found += inline(restore(value))
            elif flag == "f":
                sql = True
                if value in STDIN_PATHS:
                    stdin = True
                elif unseen(value):
                    unread.append(restore(value) or "-f with no file")
                else:
                    found += read(restore(value))
    if not stdin and (sql or info):
        return found, False, writes
    if source is None:
        return found, True, writes
    kind, target = source
    if kind == "<<<":
        if EXPANSION.search(target):
            unread.append("here-string with shell expansion")
        found += inline(restore(target))
    elif kind == "<<":
        doc = heredoc(target)
        if doc is None:
            unread.append("a heredoc the guard could not find")
        else:
            if not doc[1] and EXPANSION.search(doc[0]):
                unread.append("unquoted heredoc with shell expansion")
            found += inline(doc[0])
    elif kind == "<&" or unseen(target) or target in STDIN_PATHS:
        unread.append(restore(target) or "a redirect with no file")
    else:
        found += read(restore(target), True)
    return found, False, writes


def launched(name, args):
    if name in NAMES_PSQL:
        return None
    for i, word in enumerate(args):
        if os.path.basename(restore(word)) == "psql":
            return args[i + 1 :]
    return None


def written(name, args):
    targets = [restore(args[i + 1]) for i, w in enumerate(args[:-1]) if w in OUT_REDIRECTS or w == "<>"]
    if name in READ_ONLY:
        return targets
    return targets + [part for w in args for part in restore(w).split("=")]


def touches(writes, cwds, loaded):
    hits = []
    for word in writes:
        for place in cwds:
            if not word or not place:
                continue
            path = os.path.realpath(os.path.join(place, os.path.expanduser(word)))
            if any(f == path or f.startswith(path.rstrip(os.sep) + os.sep) for f in loaded):
                hits.append(word)
                break
    return hits
