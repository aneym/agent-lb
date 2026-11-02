import functools
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
RSYNC_VALUED = {
    "-e",
    "--rsh",
    "-f",
    "--filter",
    "--exclude",
    "--include",
    "--exclude-from",
    "--include-from",
    "--files-from",
    "-T",
    "--temp-dir",
    "--partial-dir",
    "--backup-dir",
    "--compare-dest",
    "--copy-dest",
    "--link-dest",
    "--rsync-path",
    "--log-file",
    "--password-file",
    "--chmod",
    "--chown",
    "--suffix",
    "--port",
    "-B",
}
GIT_VALUED = {"-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"}
MOVES = {"cd", "pushd", "popd"}
SHELL_FILES = (".bashrc", ".bash_aliases", ".bash_profile", ".profile", ".zshrc", ".zsh_aliases", ".zprofile")
DEFINED_ALIAS = re.compile(r"(?:^|[\s;&|({])alias\s+(?:-\S+\s+)*([A-Za-z_][\w.:-]*)=(?:'([^']*)'|\"([^\"]*)\"|(\S*))")
DEFINITION = re.compile(r"(?:^|[\s;&|({])(?:function\s+([A-Za-z_][\w.:-]*)|([A-Za-z_][\w.:-]*)\s*\(\s*\))")
FIND_EXEC = {"-exec", "-execdir", "-ok", "-okdir"}
FIND_SYNTAX = {"(", ")", "!", ",", "{}", "+", ";", "\\;"}
ASSIGNMENT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", re.S)
REFUSAL = (
    "this deletes a data path (a database, /var/lib, postgres, T3 state or an approval directory) or a target "
    "it cannot resolve. Only a grandfathered data-delete record passes it until Rails spends a sealed approval "
    "for one target."
)
VARIABLE_MARK = "$UNRESOLVED"
VARIABLE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)(?::\?[^}]*)?\}|([A-Za-z_][A-Za-z0-9_]*))")


def nested(name, args):
    if name == "eval":
        return " ".join(args)
    if name == "alias":
        return "; ".join(arg.split("=", 1)[1] for arg in args if "=" in arg) or None
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


def chdir(word):
    return word in {"-C", "-D", "--chdir"} or word.startswith(("--chdir=", "--work-tree", "--git-dir"))


def found_aliases(text):
    return {name: single or double or bare for name, single, double, bare in DEFINED_ALIAS.findall(text)}


@functools.lru_cache(maxsize=None)
def shell_aliases():
    home = os.path.expanduser("~")
    files = [os.path.join(home, name) for name in SHELL_FILES]
    files += sorted(glob.glob(os.path.join(home, ".claude/shell-snapshots/*.sh")), key=os.path.getmtime)[-3:]
    aliases = {}
    for path in files:
        try:
            aliases.update(found_aliases(open(path, errors="replace").read()))
        except OSError:
            continue
    return aliases


def mentions(text, words):
    if any(word in text for word in words):
        return True
    risky = [name for name, body in shell_aliases().items() if any(word in body for word in words)]
    return any(re.search(r"(?<![\w.:-])" + re.escape(name) + r"(?![\w.:-])", text) for name in risky)


def defined(cmd):
    return {a or b for a, b in DEFINITION.findall(cmd)}


def git_targets(args):
    base, hidden, i = ".", False, 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] == "-C" and i + 1 < len(args):
            base = os.path.join(base, args[i + 1])
        hidden = hidden or args[i].startswith(("--git-dir", "--work-tree"))
        i += 2 if args[i] in GIT_VALUED or args[i] == "-C" else 1
    rest = args[i + 1 :] if i < len(args) and args[i] == "clean" else None
    forced = rest is not None and any(a == "--force" or (a[:1] == "-" and a[1:2] != "-" and "f" in a) for a in rest)
    if not forced:
        return None
    if hidden:
        return [VARIABLE_MARK]
    specs = rest[rest.index("--") + 1 :] if "--" in rest else [a for a in rest if not a.startswith("-")]
    return [base, *(os.path.join(base, spec) for spec in specs)]


def targets(name, args):
    if name in DELETERS:
        return operands(name, args)
    if name == "busybox" and args:
        return targets(os.path.basename(args[0]), args[1:])
    if name == "git":
        return git_targets(args)
    if name == "rsync":
        if not any(a == "--del" or a.startswith(("--delete", "--remove-source-files")) for a in args):
            return None
        found, skip = [], False
        for arg in args:
            if skip:
                skip = False
            elif arg.startswith("-"):
                skip = arg in RSYNC_VALUED
            else:
                found.append(arg)
        return found
    if name == "mv":
        found, into, skip = [], [], None
        for arg in args:
            if skip is not None:
                (into if skip else []).append(arg)
                skip = None
            elif arg in {"-t", "--target-directory", "-S", "--suffix"}:
                skip = arg in {"-t", "--target-directory"}
            elif arg.startswith(("--target-directory=", "-t")):
                into.append(arg.split("=", 1)[1] if "=" in arg else arg[2:])
            elif not arg.startswith("-"):
                found.append(arg)
        return into or found[-1:]
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
    if name not in MOVES:
        return cwd
    rest = [word for word in words[words.index(name) + 1 :] if not word.startswith("-")] if name in words else []
    if name == "popd" or len(rest) != 1:
        return None if name == "popd" or rest else os.path.expanduser("~")
    return located(rest[0], assignments, cwd)
