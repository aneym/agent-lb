import os
import re
import shlex
import socket

MARKER = re.compile(
    r"^[\s>*_-]*Needs your yes on ([A-Za-z0-9][A-Za-z0-9-]*)\s*:\s*(.*?)\s*$",
    re.MULTILINE | re.IGNORECASE,
)
HOW = (
    "next: a T3 message cannot prove who typed it, so this floor lifts only on an answered owner ask. File one with "
    'ask(to:"owner", question, options) whose first option is the Recommended one and opens with Delete, Yes or '
    'Allow, as in "Delete it (Recommended)"; a delete names its paths in the question, any other act holds the line '
    "`Needs your yes on <this host>: <the exact command>`. Cite it in the record with "
    "Ask: <ask id> (on a delete, Ask: <ask id> target <host>:<path>[,<path>...]) and Covers: <floor>."
)
MACHINES = ("ax102", "ax42", "studio", "forge", "book", "macbook", "macbook-ts")
SEPARATORS = {";", "&&", "||", "|"}
PIPE = "|"
LITERAL = re.compile(r"[$`*?\[\]{}()<>&|#\\!]")
SPLIT = re.compile(r"[@:=,/]")
BARRED = {
    "sh",
    "bash",
    "zsh",
    "dash",
    "ksh",
    "fish",
    "busybox",
    "eval",
    "exec",
    "source",
    ".",
    "xargs",
    "ssh",
    "scp",
    "rsync",
    "sudo",
    "doas",
    "env",
    "command",
    "nohup",
    "time",
    "nice",
    "timeout",
    "stdbuf",
    "setsid",
    "ionice",
    "cd",
    "pushd",
    "popd",
    "python",
    "python3",
    "node",
    "perl",
    "ruby",
    "bun",
    "deno",
    "tsx",
    "php",
    "watch",
    "parallel",
}
OPTIONS = re.compile(r"-[rRfdv]+|--")
PATH_TOOLS = {"rm", "rmdir", "unlink", "shred", "srm", "trash", "agent-rm"}
DELETE_TOOLS = PATH_TOOLS | {"find", "truncate", "mv"}
SQL_TOOLS = {
    "psql",
    "sqlite3",
    "mysql",
    "mariadb",
    "mongosh",
    "mongo",
    "redis-cli",
    "wrangler",
}
SQL_FILES = {"-i", "--init", "-init", "-cmd", "source"}
SQL_FILE = re.compile(r"-[A-Za-z0-9]*f|--fi|--init")


class Refused(Exception):
    pass


def here():
    return socket.gethostname().split(".")[0].lower()


def tokens(command):
    if "\n" in command.strip():
        raise Refused(
            "the command spans more than one line; the line he says yes to is one line"
        )
    lexer = shlex.shlex(command.strip(), posix=True, punctuation_chars=";&|<>()")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        raise Refused("the command does not parse as one shell line") from None


def parts(words):
    found, part, piped = [], [], False
    for word in words:
        if word in SEPARATORS:
            found.append((part, piped))
            part, piped = [], word == PIPE
        else:
            part.append(word)
    found.append((part, piped))
    if any(not part for part, _ in found):
        raise Refused("the command has an empty part around ; && || or |")
    return found


def names(part):
    return {os.path.basename(word) for word in part}


def literal(part, piped=False):
    if "=" in part[0] or os.path.basename(part[0]) in BARRED:
        raise Refused(
            f"the command part {' '.join(part)[:60]!r} runs {part[0]!r}, which hides what it acts on"
        )
    for word in part:
        if LITERAL.search(word):
            raise Refused(
                f"the argument {word[:60]!r} is not literal (a $, glob, brace, backtick, redirect, comment or escape)"
            )
        if any(
            piece.lower() in MACHINES and piece.lower() != here()
            for piece in SPLIT.split(word)
        ):
            raise Refused(f"the argument {word[:60]!r} reaches another host")
    if names(part) & DELETE_TOOLS:
        for word in part[1:]:
            if word.startswith("-") or os.path.basename(word) in DELETE_TOOLS:
                continue
            if not word.startswith(("/", "~/")):
                raise Refused(f"the delete target {word[:60]!r} is not a full path")
            if {".", ".."} & set(word.split("/")):
                raise Refused(f"the delete target {word[:60]!r} holds . or ..")
    if names(part) & SQL_TOOLS:
        if piped:
            raise Refused(
                "the SQL tool is fed by a pipe, so the SQL it runs is not on the line he saw"
            )
        for word in part[1:]:
            if (
                word in SQL_FILES
                or SQL_FILE.match(word)
                or re.search(r"\bsource\b", word, re.IGNORECASE)
            ):
                raise Refused(
                    f"the SQL tool reads SQL from a file ({word[:40]}), which the line he saw does not show"
                )
            if re.match(r"\s*\.(?:read|import|restore|shell|system)\b", word):
                raise Refused(f"the SQL text {word[:40]!r} runs a dot command")
            if len([text for text in word.split(";") if text.strip()]) > 1:
                raise Refused(
                    f"the SQL text {word[:40]!r} holds more than one statement; one yes covers one"
                )
    elif any(";" in word for word in part):
        raise Refused(
            f"the command part {' '.join(part)[:60]!r} hides a ; inside an argument"
        )


def asked(text):
    lines = MARKER.findall(text or "")
    if len(lines) != 1:
        raise Refused(
            "the message he answered holds "
            + ("no" if not lines else "more than one")
            + " line `Needs your yes on <host>: <the exact command>`"
        )
    host, line = lines[0]
    if len(line) > 1 and line[0] == line[-1] == "`":
        line = line[1:-1]
    return host.lower(), line


def covers(text, command):
    host, line = asked(text)
    if host != here():
        raise Refused(
            f"the line he said yes to names {host}, and this runs on {here()}"
        )
    if not command or not command.strip():
        raise Refused("there is no command to hold against the line he said yes to")
    ran, shown = tokens(command), tokens(line)
    if ran != shown:
        raise Refused(
            f"the command is not the one he said yes to on {host} ({' '.join(shown)[:80]!r}); each chained part, "
            "argument and target must be the same"
        )
    for part, piped in parts(ran):
        literal(part, piped)
    return host


def listed(host, names, command):
    if host != here():
        raise Refused(f"the record's target is on {host}, and this runs on {here()}")
    if not names:
        raise Refused("the record's Ask line lists no target path")
    clean = set()
    for name in names:
        if (
            not name.startswith("/")
            or LITERAL.search(name)
            or {".", ".."} & set(name.rstrip("/").split("/")[1:])
        ):
            raise Refused(
                f"the record's target {name[:60]!r} is not a literal full path"
            )
        clean.add(name.rstrip("/") or "/")
    if not command or not command.strip():
        raise Refused("there is no command to hold against the record's targets")
    found = parts(tokens(command))
    if len(found) != 1:
        raise Refused(
            "the command chains more than one command; the record's targets cover one delete"
        )
    part = found[0][0]
    literal(part)
    if os.path.basename(part[0]) not in PATH_TOOLS:
        raise Refused(f"the command runs {part[0]!r}, not a delete of the listed paths")
    for word in part[1:]:
        if OPTIONS.fullmatch(word):
            continue
        if word.startswith("-"):
            raise Refused(f"the command carries the extra argument {word[:40]!r}")
        if (word.rstrip("/") or "/") not in clean:
            raise Refused(
                f"the command touches {word[:80]}, which the record's target list does not list"
            )
