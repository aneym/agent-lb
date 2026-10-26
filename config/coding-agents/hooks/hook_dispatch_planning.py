import hashlib
import marshal
import os
import re
import shutil
import sys
import tempfile
import types

DEFAULT_TIMEOUT = 600.0

FLOOR_TIMEOUT = 40.0

SHELL = "/bin/sh"

HERE = os.path.dirname(os.path.abspath(__file__))

REWRITERS = {"rtk hook claude"}

REV_SHA = re.compile(r"[0-9a-f]{64}")

FLOOR_TEXT = "BLOCKED: Dangerous command"

INLINE_FLOOR = "dangerous-command"

RECEIPT_ENV = "HOOK_FLOOR_RECEIPT"

ENV_SENSITIVE = (
    "BASH_ENV",
    "ENV",
    "BASHOPTS",
    "SHELLOPTS",
    "POSIXLY_CORRECT",
    "GREP_OPTIONS",
    "GLOBIGNORE",
    "LD_PRELOAD",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH",
)

HAZARD = re.compile(
    r"os\._exit|os\.fork|os\.exec|os\.setsid|os\.dup2|os\.closerange|os\.kill\(\s*os\.getpid|threading|_thread|"
    r"multiprocessing|concurrent\.futures|asyncio|atexit|\bsignal\.|faulthandler|ctypes|sys\.settrace|sys\.setprofile|"
    r"except\s*:|except\s+BaseException|except\s*\(\s*BaseException|sys\.modules\[\s*['\"]__main__|importlib\.reload|"
    r"sys\.__std(?:in|out|err)__|os\.close\(\s*[012]\s*\)"
)

DUP2_STD = re.compile(r"os\.dup2\((?:[^()]|\([^()]*(?:\([^()]*\))?[^()]*\))*,\s*[012]\s*\)")

IMPORT_NAMES = re.compile(
    r"^\s*(?:from\s+([A-Za-z_][\w.]*)\s+import|import\s+([A-Za-z_][\w.]*(?:\s*,\s*[A-Za-z_][\w.]*)*))", re.M
)

_PATH_WORD = (
    r'"\$HOME/[^"$`\\]*"|\$HOME/[^\s"\'$`\\;&|<>(){}*?\[\]]*|~/[^\s"\'$`\\;&|<>(){}*?\[\]]*|'
    r'"/[^"$`\\]*"|/[^\s"\'$`\\;&|<>(){}*?\[\]]+'
)

_ARG_WORD = r"[A-Za-z0-9_./=:+-]+"

SHAPE = re.compile(
    r"^\s*(?:(?P<interp>/usr/bin/python3|python3)\s+)?(?P<script>%s)(?P<args>(?:\s+%s)*)"
    r"(?P<devnull>\s+2>\s*/dev/null)?"
    r"(?:\s+\|\|\s+(?:(?P<true>true)|\{\s*printf\s+%%s\s+'(?P<fb>[^']*)'\s*;\s*\}))?\s*$" % (_PATH_WORD, _ARG_WORD)
)


FLOOR_EXEC = re.compile(
    r"^\s*(?:(?P<interp>/usr/bin/python3|python3|/bin/bash|bash)\s+)?(?P<script>%s)(?P<args>(?:\s+%s)*)\s*$"
    % (_PATH_WORD, _ARG_WORD)
)


def env_sensitive():
    for key in ENV_SENSITIVE:
        if key in os.environ:
            return key
    for key in os.environ:
        if key.startswith("BASH_FUNC_"):
            return key
    return None


def which(name):
    return shutil.which(name, path=os.environ.get("PATH", os.defpath))


def utf8_locale():
    for key in ("LC_ALL", "LC_CTYPE", "LANG"):
        value = os.environ.get(key)
        if value:
            return bool(re.search(r"utf-?8", value, re.I))
    return False


def expand_word(word):
    if word.startswith('"') and word.endswith('"'):
        word = word[1:-1]
    home = os.environ.get("HOME", "")
    if word.startswith("$HOME/"):
        return home + word[len("$HOME") :]
    if word.startswith("~/"):
        return home + word[1:]
    return word


def interpreter_of(script):
    try:
        with open(script, "rb") as handle:
            first = handle.readline(256)
    except OSError:
        return None
    if not first.startswith(b"#!"):
        return None
    parts = first[2:].decode("utf-8", "replace").split()
    if not parts:
        return None
    if os.path.basename(parts[0]) == "env" and len(parts) > 1:
        return parts[1]
    return parts[0]


def same_interpreter(interp):
    if interp is None:
        return False
    path = interp if os.path.isabs(interp) else which(interp)
    if not path:
        return False
    try:
        return os.path.realpath(path) == os.path.realpath(sys.executable)
    except OSError:
        return False


def python_like(interp):
    return interp is not None and re.fullmatch(r"python3(\.\d+)?", os.path.basename(interp)) is not None


def sha256_file(path):
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None


def sibling_sources(script_dir, source, depth=0, seen=None):
    seen = seen if seen is not None else set()
    found = []
    for match in IMPORT_NAMES.finditer(source):
        names = [match.group(1)] if match.group(1) else [n.strip() for n in match.group(2).split(",")]
        for name in names:
            top = name.split(".")[0]
            if top in seen:
                continue
            for candidate in (os.path.join(script_dir, top + ".py"), os.path.join(script_dir, top, "__init__.py")):
                if os.path.isfile(candidate):
                    seen.add(top)
                    try:
                        with open(candidate, encoding="utf-8") as handle:
                            text = handle.read()
                    except (OSError, UnicodeDecodeError):
                        text = "os._exit  # unreadable"
                    found.append(text)
                    if depth < 2:
                        found.extend(sibling_sources(os.path.dirname(candidate), text, depth + 1, seen))
                    break
    return found


def hazardous(text):
    for match in HAZARD.finditer(text):
        if match.group(0) == "os.dup2" and DUP2_STD.match(text, match.start()):
            continue
        return True
    return False


def cached(key, build, kind):
    path = os.path.join(HERE, "__pycache__", "hook-dispatch-" + key)
    try:
        with open(path, "rb") as handle:
            value = marshal.load(handle)
        if isinstance(value, kind):
            return value
    except (OSError, EOFError, ValueError, TypeError):
        pass
    value = build()
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=os.path.dirname(path))
        with os.fdopen(fd, "wb") as handle:
            marshal.dump(value, handle)
        os.replace(temp, path)
    except (OSError, ValueError):
        pass
    return value


def guard_code(plan):
    key = hashlib.sha256((plan.script + "\0" + plan.source).encode("utf-8")).hexdigest()
    tag = sys.implementation.cache_tag or "python"
    return cached(
        "code-%s-%s" % (tag, key), lambda: compile(plan.source, plan.script, "exec", dont_inherit=True), types.CodeType
    )
