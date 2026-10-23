import os
import re
import shlex

from no_chrome_argv import TABLES, parse  # type: ignore

SSH_VALUE = set("BbcDEeFIiJLlmOopQRSWw")
SCP_VALUE = set("cFiJloPSX")
WRAPPERS = {
    "xcrun": ("", {"verbose", "log", "no-cache", "kill-cache"}, {"sdk", "toolchain"}, "vlkn"),
    "arch": ("", {"help"}, {"arch"}, ""),
    "env": (
        "uCSPa",
        {"ignore-environment", "null", "debug"},
        {"unset", "chdir", "split-string", "argv0"},
        "iv0",
    ),
    "timeout": (
        "sk",
        {"foreground", "preserve-status", "verbose"},
        {"signal", "kill-after"},
        "v",
    ),
    "nice": ("n", set(), {"adjustment"}, ""),
    "nohup": ("", set(), set(), ""),
    "stdbuf": ("ioe", set(), {"input", "output", "error"}, ""),
    "caffeinate": ("tw", set(), set(), "dimsu"),
    "exec": ("a", set(), set(), "cl"),
    "command": ("", set(), set(), "p"),
    "sudo": (
        "uUghpCrDtTR",
        {"preserve-env", "set-home", "non-interactive", "stdin"},
        {
            "user",
            "group",
            "host",
            "prompt",
            "chdir",
            "chroot",
            "role",
            "type",
            "command-timeout",
            "close-from",
        },
        "AbEHknPS",
    ),
    "xvfb-run": (
        "nefswp",
        {"auto-servernum", "listen-tcp"},
        {"server-num", "error-file", "auth-file", "server-args", "xauth-protocol"},
        "al",
    ),
    "time": ("fo", {"append", "portability", "verbose"}, {"format", "output"}, "apv"),
}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
ARRAY_START = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")
DURATION = re.compile(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)[smhd]?")
SHELLS = {"bash", "sh", "zsh", "dash"}
KEYWORDS = {"if", "then", "do", "while", "until", "else", "elif", "!", "{"}
OPERATORS = "<>;&|()"
HEREDOC = re.compile(r"(?<!<)<<(?!<)-?\s*(\\?)(['\"]?)([A-Za-z0-9_.-]+)\2")
INTERPRETERS = {
    "node",
    "nodejs",
    "python",
    "python3",
    "deno",
    "bun",
    "ruby",
    "perl",
    "npx",
    "tsx",
    "osascript",
}
FIND_EXEC = {"-exec", "-execdir", "-ok", "-okdir"}
REDIRECT = re.compile(r"[0-9]*(>>?|<>?|>&|<&|&>>?)")
HOME_REF = re.compile(r"\$(HOME\b|\{HOME\})")
OTHER_USER = re.compile(r"\b(su|sudo|doas|runuser|machinectl|ssh|scp)\b")


class Doubt(Exception):
    pass


def normalize(argv):
    while argv:
        if ASSIGNMENT.match(argv[0]):
            argv = argv[1:]
            continue
        name = os.path.basename(argv[0])
        if name not in WRAPPERS:
            if name == "xargs" or argv[0].startswith("-") or any(c in argv[0] for c in "$`\\"):
                raise Doubt
            return argv
        if argv[1:] == ["--help"] or name == "env" and len(argv) == 1:
            return ["type"] + argv[1:]
        if name == "command" and argv[1:2] in (["-v"], ["-V"]):
            return ["type"] + argv[2:]
        values, long_flags, long_values, flags = WRAPPERS[name]
        i = 1
        while i < len(argv):
            arg = argv[i]
            if arg == "--":
                i += 1
                break
            if name == "arch" and arg in ("-arm64", "-x86_64", "-i386"):
                i += 1
                continue
            if name == "env" and (ASSIGNMENT.match(arg) or arg == "-"):
                i += 1
                continue
            if name == "nice" and re.fullmatch(r"-[0-9]+", arg):
                i += 1
                continue
            if not arg.startswith("-") or arg == "-":
                break
            split, value = False, ""
            if arg.startswith("--"):
                option, equals, value = arg[2:].partition("=")
                if option in long_values:
                    if not equals:
                        i += 1
                        if i >= len(argv):
                            raise Doubt
                        value = argv[i]
                    split = name == "env" and option == "split-string"
                elif option not in long_flags or equals:
                    raise Doubt
            else:
                for j, flag in enumerate(arg[1:], 1):
                    if flag in values:
                        value = arg[j + 1 :]
                        if not value:
                            i += 1
                            if i >= len(argv):
                                raise Doubt
                            value = argv[i]
                        split = name == "env" and flag == "S"
                        break
                    if flag not in flags:
                        raise Doubt
            i += 1
            if split:
                if any(c in value for c in "$`\\"):
                    raise Doubt
                try:
                    argv = ["env"] + shlex.split(value) + argv[i:]
                except ValueError:
                    raise Doubt
                return normalize(argv)
        if name == "timeout":
            if i >= len(argv) or not DURATION.fullmatch(argv[i]):
                raise Doubt
            i += 1
        argv = argv[i:]
        if not argv:
            raise Doubt
    return []


def consumer_of(prefix):
    part = re.split(r"[;&|(]", prefix)[-1]
    try:
        words = shlex.split(part, comments=False)
    except ValueError:
        words = part.split()
    return normalize(words)


def host_of(destination):
    if destination.startswith(("ssh://", "scp://")):
        destination = destination.split("://", 1)[1].split("/", 1)[0]
    host = destination.rsplit("@", 1)[-1]
    if host.startswith("["):
        host = host[1:].split("]", 1)[0]
    return host.split(":", 1)[0].lower()


def operands(args, takes_value):
    out, i = [], 0
    while i < len(args):
        arg = args[i]
        if out or arg == "--" or not arg.startswith("-") or arg == "-":
            if arg == "--" and not out:
                out.extend(args[i + 1 :])
                break
            out.append(arg)
        else:
            for j, flag in enumerate(arg[1:]):
                if flag in takes_value:
                    if j == len(arg) - 2:
                        i += 1
                    break
        i += 1
    return out


def unquoted_starts(line, quote, comments=None):
    free, k = set(), 0
    while k < len(line):
        c = line[k]
        if quote == "'":
            quote = None if c == "'" else quote
        elif quote == '"':
            if c == "\\":
                k += 1
            elif c == '"':
                quote = None
        elif c == "\\":
            k += 1
        elif c in "'\"":
            quote = c
        elif c == "#" and (k == 0 or line[k - 1] in " \t\n;&|()"):
            end = line.find("\n", k)
            if comments is not None:
                comments.update(range(k, len(line) if end < 0 else end))
            if end < 0:
                break
            k = end
            continue
        else:
            free.add(k)
        k += 1
    return free, quote


def heredoc_split(text):
    text = text.replace("\\\n", "")
    out, bodies, code, ends, quote = [], [], [], [], None
    for line in text.split("\n"):
        if ends:
            end, live, words, body = ends[0]
            if line.strip() == end or line.lstrip("\t") == end:
                ends.pop(0)
                name = os.path.basename(words[0]) if words else ""
                if reads_stdin(name, without_redirects(words[1:]), False):
                    code.append((name, "\n".join(body)))
            else:
                body.append(line)
                if live:
                    bodies.append(line)
            continue
        out.append(line)
        free, next_quote = unquoted_starts(line, quote)
        if quote is None:
            ends += [
                (
                    m.group(3),
                    not (m.group(1) or m.group(2)),
                    consumer_of(line[: m.start()]),
                    [],
                )
                for m in HEREDOC.finditer(line)
                if m.start() in free
            ]
        quote = next_quote
    if ends:
        raise Doubt
    return "\n".join(out), "\n".join(bodies), code


def substitutions(text):
    bodies, out, i, quote = [], [], 0, None
    while i < len(text):
        c = text[i]
        if c == "\\" and quote != "'":
            out.append(text[i : i + 2])
            i += 2
            continue
        if c in "'\"" and (quote is None or quote == c):
            quote = None if quote else c
        if quote != "'" and (c == "`" or text.startswith("$(", i)):
            backtick = c == "`"
            start = i + (1 if backtick else 2)
            k, level, inner = start, 1, None
            while k < len(text):
                char = text[k]
                if char == "\\":
                    k += 2
                    continue
                if backtick:
                    if char == "`":
                        break
                elif char in "'\"" and (inner is None or inner == char):
                    inner = None if inner else char
                elif inner is None:
                    level += {"(": 1, ")": -1}.get(char, 0)
                    if level == 0:
                        break
                k += 1
            if k >= len(text):
                raise Doubt
            bodies.append(text[start:k])
            out.append("$substitution")
            i = k + 1
            continue
        out.append(c)
        i += 1
    return bodies, "".join(out)


def simple_commands(text, redirects=None, arrays=None):
    if any(0xE000 <= ord(c) < 0xE000 + len(OPERATORS) for c in text):
        raise Doubt
    comments = set()
    free, _ = unquoted_starts(text, None, comments)
    text = "".join(
        ""
        if k in comments
        else ("\n ; " if c == "\n" else c)
        if k in free
        else chr(0xE000 + OPERATORS.index(c))
        if c in OPERATORS
        else c
        for k, c in enumerate(text)
    )
    restore = {0xE000 + i: c for i, c in enumerate(OPERATORS)}
    lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        words = list(lexer)
    except ValueError:
        raise Doubt
    command, target, here, piped, array = [], False, [], False, False
    array_values = []
    for word in words + [";"]:
        if array:
            if word and set(word) <= set(";&|()<>"):
                if word[0] != ")" or "(" in word or "<" in word or ">" in word:
                    raise Doubt
                if arrays is not None:
                    arrays.append((command, array_values))
                array, word = False, word[1:]
                if not word:
                    continue
            else:
                array_values.append(word.translate(restore))
                continue
        if word.startswith("()") and not target and len(command) == 1 and ARRAY_START.fullmatch(command[0]):
            if arrays is not None:
                arrays.append((command, []))
            word = word[2:]
            if not word:
                continue
        if word == "(" and not target and len(command) == 1 and ARRAY_START.fullmatch(command[0]):
            array = word == "("
            array_values = []
            if not array and arrays is not None:
                arrays.append((command, []))
            continue
        if word and set(word) <= set(";&|()<>") and ("<" in word or ">" in word) and "(" not in word:
            if command and command[-1].isdigit():
                command.pop()
            target = "here" if word == "<<<" else True
        elif word and set(word) <= set(";&|()<>"):
            if command:
                yield command, here, piped
            command, target, here, piped = [], False, [], word in ("|", "|&")
        elif target:
            if redirects is not None and target != "here":
                redirects.append(word.translate(restore))
            if target == "here":
                here.append(word.translate(restore))
            target = False
        else:
            command.append(word.translate(restore))


def shell_args(args):
    i, flags = 0, set()
    while i < len(args):
        arg = args[i]
        if arg == "--":
            i += 1
            break
        if arg in ("--rcfile", "--init-file"):
            i += 2
            continue
        if not arg.startswith(("-", "+")) or arg in ("-", "+"):
            break
        if not arg.startswith("--"):
            change = flags.add if arg[0] == "-" else flags.discard
            for flag in arg[1:]:
                change(flag)
            if "o" in arg[1:] or "O" in arg[1:]:
                i += 1
                if "o" in arg[1:] and i < len(args) and args[i].replace("_", "").lower() == "noexec":
                    change("n")
        i += 1
    return flags, args[i:]


def shell_string(args):
    flags, rest = shell_args(args)
    if "c" not in flags:
        return None
    if not rest:
        raise Doubt
    return rest[0]


def reads_stdin(name, args, noexec=True):
    if name in SHELLS:
        flags, rest = shell_args(args)
        return (
            "c" not in flags
            and not (noexec and "n" in flags and "i" not in flags)
            and ("s" in flags or not rest or rest[0] == "-")
        )
    if name not in INTERPRETERS and name not in TABLES:
        return False
    return parse(name, args)[1]


def without_redirects(words):
    out, skip = [], False
    for word in words:
        if skip:
            skip = False
        elif REDIRECT.fullmatch(word):
            skip = True
        elif not REDIRECT.match(word):
            out.append(word)
    return out


def pin_home(text):
    home = os.path.expanduser("~")
    shell, live, code = heredoc_split(text)
    run = "\n".join([shell] + substitutions(live)[0] + [body for _, body in code])
    if "HOME" in HOME_REF.sub("", run) or OTHER_USER.search(run) or not re.fullmatch(r"/[A-Za-z0-9_/.-]+", home):
        return text
    return HOME_REF.sub(home, text)


def find_execs(args):
    segments, i = [], 0
    while i < len(args):
        if args[i] in FIND_EXEC:
            end = i + 1
            while end < len(args) and not (args[end] == ";" or args[end] == "+" and args[end - 1] == "{}"):
                end += 1
            if end >= len(args) or end == i + 1:
                raise Doubt
            segments.append(["$found" if a == "{}" else a for a in args[i + 1 : end]])
            i = end
        i += 1
    return segments


VARIABLE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")


def resolve_value(value, variables):
    def replace(match):
        return variables.get(match[1] or match[2], match[0])

    value = VARIABLE.sub(replace, value)
    if value.startswith("~/"):
        value = os.path.expanduser("~") + value[1:]
    return value


def value_path(value, variables):
    value = resolve_value(value, variables)
    if not os.path.isabs(value) or any(c in value for c in ("$", "`", chr(92))):
        return None
    return os.path.realpath(value)


def browser_roots():
    library = os.path.expanduser("~/Library")
    apps = (
        "Google/Chrome",
        "Chromium",
        "BraveSoftware",
        "Microsoft Edge",
        "Vivaldi",
        "Arc",
        "Aside",
        "Firefox",
    )
    return [os.path.realpath(library + "/Application Support/" + app) for app in apps] + [
        os.path.realpath(library + "/" + app)
        for app in (
            "Safari",
            "Cookies",
            "Containers/com.apple.Safari",
            "HTTPStorages",
            "WebKit",
        )
    ]


def owner_path(value, variables, ancestor=False):
    path = value_path(value, variables)
    if path is None:
        return False
    for root in browser_roots():
        if path == root or path.startswith(root + "/") or root.endswith("/Aside") and path.startswith(root):
            return True
        if ancestor and root.startswith(path.rstrip("/") + "/"):
            return True
    return False


def segment_values(raw, variables):
    values = []
    for word in raw:
        if ASSIGNMENT.match(word):
            key, value = word.split("=", 1)
            variables[key] = resolve_value(value, variables)
            values.append(variables[key])
        else:
            values.append(resolve_value(word, variables))
    return values


def profile_tainted(argv, writes):
    profiles = [a.partition("=")[2] for a in argv if a.startswith("--user-data-dir=")]
    profiles += [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "--user-data-dir"]
    for profile in profiles:
        path = value_path(profile, {})
        if path is None or any(path == write or path.startswith(write.rstrip("/") + "/") for write in writes):
            return True
    return False


def record_writes(argv, writes):
    name = os.path.basename(argv[0])
    if name not in {"ln", "cp", "mv", "rsync", "ditto", "tar", "unzip"}:
        return
    if name == "tar" and not any("x" in a for a in argv[1:] if a.startswith("-") or a == "x"):
        return
    paths = [value_path(a, {}) for a in argv[1:] if not a.startswith("-")]
    writes.extend(path for path in paths if path)
