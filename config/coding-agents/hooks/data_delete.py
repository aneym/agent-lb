import glob
import os
import re

DATA = re.compile(
    r"(?:^|/)(?:var/lib|pgdata|postgres(?:ql)?)(?:/|$)"
    r"|\.(?:sqlite3?|db)(?:-wal|-shm|-journal)?$"
    r"|statev2"
    r"|/\.agent-rails/(?:approvals|custody)(?:/|$)"
    r"|/\.open-factory/approvals(?:/|$)"
    r"|/\.t3/userdata(?:/|$)",
    re.I,
)
DELETERS = {"rm", "shred", "truncate", "unlink"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
FEEDERS = {"xargs", "parallel"}
VALUED = {
    "truncate": {"-s", "--size", "-r", "--reference"},
    "shred": {"-n", "--iterations", "-s", "--size", "--random-source"},
}
SSH_VALUED = {
    "-b",
    "-c",
    "-D",
    "-E",
    "-e",
    "-F",
    "-I",
    "-i",
    "-J",
    "-L",
    "-l",
    "-m",
    "-O",
    "-o",
    "-p",
    "-Q",
    "-R",
    "-S",
    "-W",
    "-w",
}
FIND_EXEC = {"-exec", "-execdir", "-ok", "-okdir"}
FIND_SYNTAX = {"(", ")", "!", ",", "{}", "+", ";", "\\;"}
ASSIGNMENT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", re.S)
REFUSAL = (
    "this deletes a data path (a database, /var/lib, postgres, T3 state or an approval directory) or a target "
    "it cannot resolve. Only a grandfathered data-delete record passes it until Rails spends a sealed approval "
    "for one target."
)
VARIABLE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)(?::\?[^}]*)?\}|([A-Za-z_][A-Za-z0-9_]*))")


def nested(name, args):
    if name == "eval":
        return " ".join(args)
    if name in SHELLS:
        for i, arg in enumerate(args[:-1]):
            if arg[:1] == "-" and arg[1:2] != "-" and "c" in arg[1:]:
                return args[i + 1]
        return None
    if name == "ssh":
        i = 0
        while i < len(args) and args[i].startswith("-"):
            i += 2 if args[i] in SSH_VALUED else 1
        return " ".join(args[i + 1 :]) or None
    return None


def operands(name, args):
    found, skip = [], False
    for i, arg in enumerate(args):
        if skip:
            skip = False
        elif arg == "--":
            return found + args[i + 1 :]
        elif arg.startswith("-") and len(arg) > 1:
            skip = arg in VALUED.get(name, set())
        else:
            found.append(arg)
    return found


def targets(name, args):
    if name in DELETERS:
        return operands(name, args)
    if name != "find":
        return None
    deleting = any(
        arg == "-delete"
        or (arg in FIND_EXEC and i + 1 < len(args) and os.path.basename(args[i + 1]) in DELETERS | SHELLS | FEEDERS)
        for i, arg in enumerate(args)
    )
    if not deleting:
        return None
    return ["."] + [arg for arg in args if not arg.startswith("-") and arg not in FIND_SYNTAX]


def resolved(word, assignments, home):
    value = word
    for match in VARIABLE.finditer(word):
        key = match[1] or match[2]
        if key in assignments:
            value = value.replace(match[0], assignments[key])
        elif key == "HOME":
            value = value.replace(match[0], home)
        else:
            return None
    if "$" in value or "`" in value:
        return None
    if value == "~" or value.startswith("~/"):
        value = home + value[1:]
    return None if value.startswith("~") else value


def located(word, assignments, cwd):
    value = resolved(word, assignments, os.path.expanduser("~"))
    if value is None or (cwd is None and not value.startswith("/")):
        return None
    return os.path.normpath(os.path.join(cwd or "/", value))


def touches(name, args, assignments, cwd):
    found = targets(name, args)
    if found is None:
        return False
    for word in found:
        path = located(word, assignments, cwd)
        if path is None:
            return True
        parts = path.split("/")
        heads = ["/".join(parts[: i + 1]) for i in range(1, len(parts))]
        paths = [path, *(match for head in heads if glob.has_magic(head) for match in glob.glob(head))]
        if any(DATA.search(p) or DATA.search(os.path.realpath(p)) for p in paths):
            return True
    return False


def remember(words, assignments, shifting):
    for word in words:
        match = ASSIGNMENT.fullmatch(word)
        if match:
            value = match[2]
            if value.startswith("$(mktemp ") or value == "$(mktemp)":
                value = os.path.join(os.environ.get("TMPDIR", "/private/tmp"), "rm-guard-temporary")
            if assignments.get(match[1], value) != value:
                shifting.add(match[1])
            assignments[match[1]] = value
    return {key: value for key, value in assignments.items() if key not in shifting}


def moved(name, words, assignments, cwd):
    if name not in {"cd", "pushd", "popd"}:
        return cwd
    rest = [word for word in words[words.index(name) + 1 :] if not word.startswith("-")] if name in words else []
    if name == "popd" or len(rest) != 1:
        return None if name == "popd" or rest else os.path.expanduser("~")
    return located(rest[0], assignments, cwd)
