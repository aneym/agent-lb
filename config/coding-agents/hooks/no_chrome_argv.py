import re
from typing import NamedTuple

REST = re.compile(r".*")
DIGITS = re.compile(r"x[0-9A-Fa-f]*|[0-9]*")
LEVEL = re.compile(r"[0-9]*|:[\w-]*")


class Spec(NamedTuple):
    short: dict
    long: dict
    whole: dict
    bounded: dict


def table(code="", value="", attached="", plain="", files="", module="", long=None, whole=None, bounded=None):
    short = {}
    for kind, letters in (
        ("code", code),
        ("value", value),
        ("attached", attached),
        ("plain", plain),
        ("file", files),
        ("module", module),
    ):
        short.update(dict.fromkeys(letters, kind))
    return Spec(short, long or {}, whole or {}, bounded or {})


NODE = table(
    long={
        "eval": "code",
        "print": "code",
        "require": "value",
        "title": "value",
        "import": "value",
        "loader": "value",
        "experimental-loader": "value",
        "input-type": "value",
        "conditions": "value",
        "env-file": "value",
        "check": "plain",
        "interactive": "plain",
        "inspect": "plain",
        "inspect-brk": "plain",
        "no-warnings": "plain",
        "enable-source-maps": "plain",
        "trace-warnings": "plain",
        "expose-gc": "plain",
        "experimental-strip-types": "plain",
        "experimental-vm-modules": "plain",
        "test": "plain",
        "watch": "plain",
        "version": "plain",
        "help": "plain",
    },
    whole={
        "-e": "code",
        "-p": "code",
        "-pe": "code",
        "-r": "value",
        "-C": "value",
        "-i": "plain",
        "-c": "plain",
        "-v": "plain",
        "-h": "plain",
    },
)
PYTHON = table(
    code="c",
    module="m",
    value="XW",
    plain="bBdEhiIOPqsSuvVxR",
    long={"help": "plain", "version": "plain", "check-hash-based-pycs": "value"},
)
PERL = table(
    code="eE",
    value="IrMm",
    attached="l0iFxdDC",
    plain="acnpsStTuUwWXvhg",
    long={"help": "plain", "version": "plain"},
    bounded={"l": DIGITS, "0": DIGITS, "d": re.compile(r"t?(:.*)?")},
)
RUBY = table(
    code="eE",
    value="IrC",
    attached="0FiWKx",
    plain="acdlnpsSvwyhU",
    bounded={"0": DIGITS, "W": LEVEL, "K": re.compile(r".?")},
    long={
        "help": "plain",
        "version": "plain",
        "verbose": "plain",
        "copyright": "plain",
        "enable": "value",
        "disable": "value",
        "encoding": "value",
        "external-encoding": "value",
        "internal-encoding": "value",
    },
)
AWK = table(
    code="e",
    value="Fvil",
    files="fE",
    attached="dDopL",
    plain="bcCghMnNOPrsStVY",
    long={
        "field-separator": "value",
        "assign": "value",
        "file": "file",
        "source": "code",
        "exec": "file",
        "include": "value",
        "load": "value",
        "traditional": "plain",
        "posix": "plain",
        "re-interval": "plain",
        "sandbox": "plain",
        "characters-as-bytes": "plain",
        "use-lc-numeric": "plain",
        "version": "plain",
        "help": "plain",
        "copyright": "plain",
        "lint": "plain",
        "optimize": "plain",
        "no-optimize": "plain",
        "bignum": "plain",
        "non-decimal-data": "plain",
        "pretty-print": "plain",
        "dump-variables": "plain",
        "profile": "plain",
        "debug": "plain",
        "gen-pot": "plain",
    },
)
SED = table(
    code="e",
    files="f",
    value="l",
    attached="iI",
    plain="nErsuzab",
    long={
        "expression": "code",
        "file": "file",
        "line-length": "value",
        "in-place": "plain",
        "null-data": "plain",
        "zero-terminated": "plain",
        "quiet": "plain",
        "silent": "plain",
        "regexp-extended": "plain",
        "separate": "plain",
        "sandbox": "plain",
        "unbuffered": "plain",
        "debug": "plain",
        "posix": "plain",
        "follow-symlinks": "plain",
        "binary": "plain",
        "help": "plain",
        "version": "plain",
    },
)
OSASCRIPT = table(code="e", value="ls")
TABLES = {
    "node": NODE,
    "nodejs": NODE,
    "tsx": NODE,
    "python": PYTHON,
    "python3": PYTHON,
    "perl": PERL,
    "ruby": RUBY,
    "osascript": OSASCRIPT,
    "sed": SED,
    "gsed": SED,
}
TABLES.update(dict.fromkeys(("awk", "gawk", "mawk", "nawk"), AWK))
PROGRAM_TEXT = {"awk", "gawk", "mawk", "nawk", "sed", "gsed"}
STDIN_FILES = {"-", "/dev/stdin", "/dev/fd/0"}


def parse(name, args):
    spec = TABLES.get(name)
    if spec is None:
        return list(args), True
    codes, state = [], {"program": False, "stdin": False}

    def take(kind, value):
        codes.append(value)
        if kind in ("code", "file", "module"):
            state["program"] = True
        if kind == "file" and value in STDIN_FILES:
            state["stdin"] = True

    def unknown(i):
        return codes + list(args[i:]), True

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            i += 1
            break
        if arg == "-" or not arg.startswith("-"):
            break
        if arg.startswith("--"):
            option, equals, value = arg[2:].partition("=")
            kind = spec.long.get(option)
            if kind is None:
                if not equals:
                    return unknown(i)
                codes.append(arg)
            elif kind == "plain":
                if equals:
                    codes.append(arg)
            else:
                if not equals:
                    i += 1
                    if i >= len(args):
                        return unknown(i - 1)
                    value = args[i]
                take(kind, value)
            i += 1
            continue
        if spec.whole:
            kind = spec.whole.get(arg)
            if kind is None:
                return unknown(i)
            if kind != "plain":
                i += 1
                if i >= len(args):
                    return unknown(i - 1)
                take(kind, args[i])
            i += 1
            continue
        j = 1
        while j < len(arg):
            kind = spec.short.get(arg[j])
            if kind is None:
                return unknown(i)
            if kind == "plain":
                j += 1
                continue
            value = arg[j + 1 :]
            if kind == "attached":
                bound = spec.bounded.get(arg[j], REST).match(value).group(0)
                if bound:
                    codes.append(bound)
                j += 1 + len(bound)
                continue
            if not value:
                i += 1
                if i >= len(args):
                    return unknown(i - 1)
                value = args[i]
            if kind == "module":
                take(kind, value)
                return codes + list(args[i + 1 :]), False
            take(kind, value)
            break
        if (
            name == "sed"
            and re.fullmatch(r"-[A-Za-z]*[iI]", arg)
            and i + 1 < len(args)
            and not args[i + 1].startswith("-")
        ):
            i += 1
            codes.append(args[i])
        i += 1
    rest = args[i:]
    if name in PROGRAM_TEXT:
        if not state["program"] and rest:
            codes.append(rest[0])
        return codes, state["stdin"]
    if not state["program"] and (not rest or rest[0] == "-"):
        state["stdin"] = True
    if name == "osascript":
        return list(args), state["stdin"]
    return codes, state["stdin"]
