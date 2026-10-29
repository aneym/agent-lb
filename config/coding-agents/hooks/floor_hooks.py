import codecs
import hashlib
import os
import re
import shlex
import stat
from pathlib import Path

FLOOR_HOOKS = {
    "branch-guard.py": "git: no force push on any branch, no hook bypass, no branch switch in a shared checkout",
    "rm-dynamic-deny": "destructive data: rm needs an approval record",
    "stash-guard": "git: no stash in a shared checkout",
    "railway-vars-guard.sh": "secrets: Railway variable values never printed",
    "link-cli-guard.sh": "secrets and his accounts: no Link CLI card or login reads",
    "plutil-guard.sh": "his accounts: no edits to macOS preference plists",
    "kill-guard": "destructive: no kills outside the agent's own processes",
    "agent-lb-bootout-guard.sh": "his accounts: the account store is never booted out",
    "dangerous-command-guard.sh": "destructive data: dangerous shell commands blocked",
    "dangerous-cmd-guard.py": "destructive data: forced recursive rm of protected roots",
    "no-chrome-guard.sh": "his accounts: no automation of his Chrome logins",
    "no_chrome_guard.py": "his accounts: the no-chrome guard's checker",
    "host-credential-guard.py": "secrets: host credential files never read",
    "gh-write-guard.py": "git: no gh writes",
    "kill-scope-guard.py": "destructive: kills limited to the agent's own scope",
    "merge-gate-guard.py": "git: no merge around the gate",
    "desktop-guard": "real people, his accounts and spend: agents stay off his desktop, where those happen",
    "wide-scan-guard.sh": "secrets: no recursive reads that print secrets",
    "hooks-off-guard.py": "every floor: no setting that switches Claude Code hooks off",
}
NON_FLOOR_GUARDS = {
    "sleep-loop-guard.py": "wait hygiene, not a kept floor",
    "seat-guard.py": "seat routing advice, not a kept floor",
    "herdr-shell-host-guard.py": "herdr pane host check, not a kept floor",
    "herdr-send-guard.py": "herdr send etiquette, not a kept floor",
    "workflow-seat-guard.py": "workflow seat routing, not a kept floor",
    "workflow-relay-guard.py": "workflow relay routing, not a kept floor",
}
FLOOR_TEXT = "BLOCKED: Dangerous command"
INLINE_FLOOR = "dangerous-command"
FLOOR_WORD = re.compile(r"(?:^|[/\s\"'])(%s)(?=$|[\s\"';|&)])" % "|".join(re.escape(name) for name in FLOOR_HOOKS))
FLOOR_STEMS = {name.rsplit(".", 1)[0] if name.endswith((".py", ".sh")) else name for name in FLOOR_HOOKS}
CLASSIFIED_STEMS = FLOOR_STEMS | {
    name.rsplit(".", 1)[0] if name.endswith((".py", ".sh")) else name for name in NON_FLOOR_GUARDS
}
FLOOR_MENTIONS = tuple(sorted(FLOOR_STEMS)) + (
    INLINE_FLOOR,
    FLOOR_TEXT,
)
ACCEPTED_FORMS = {
    'bash "$HOME/.claude/hooks/railway-vars-guard.sh"': ('"$HOME/.claude/hooks/railway-vars-guard.sh"',),
    '"$HOME/.claude/hooks/railway-vars-guard.sh"': ('bash "$HOME/.claude/hooks/railway-vars-guard.sh"',),
}


ANSI_C = re.compile(r"\$'((?:[^'\\]|\\.)*)'")


def ansi_c(command):
    def decode(match):
        try:
            return codecs.decode(match.group(1), "unicode_escape")
        except (UnicodeDecodeError, ValueError):
            return match.group(1)

    return ANSI_C.sub(decode, command)


def raw_readings(command):
    decoded = ansi_c(command)
    texts = [command, decoded]
    texts += [text.replace("\\", "").replace("'", "").replace('"', "") for text in texts]
    for text in (command, decoded):
        try:
            texts += shlex.split(text)
        except ValueError:
            pass
    return texts


def readings(command):
    return [text.lower() for text in raw_readings(command)]


MAX_DIGEST_BYTES = 1 << 20
PATH_TOKEN = re.compile(r"(?:\$HOME|\$\{HOME\}|~|/)[^\s\"';|&)<>]*")


def stem(name):
    return name.rsplit(".", 1)[0] if name.endswith((".py", ".sh")) else name


def path_tokens(command):
    home = os.path.expanduser("~")
    tokens = {token for text in raw_readings(command) for token in PATH_TOKEN.findall(text)}
    return {re.sub(r"^(?:\$HOME|\$\{HOME\}|~)(?=/|$)", home, token) for token in tokens}


def digest(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(MAX_DIGEST_BYTES + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    return hashlib.sha256(data).hexdigest() if len(data) <= MAX_DIGEST_BYTES else None


def floor_digests():
    roots = (Path(__file__).resolve().parent, Path(os.path.expanduser("~/.claude/hooks")))
    found = {}
    for root in roots:
        for name in FLOOR_HOOKS:
            value = digest(str(root / name))
            if value:
                found[value] = name
    return found


def resolved_floors(command):
    terms, digests = set(), None
    for token in path_tokens(command):
        if not os.path.lexists(token):
            continue
        real = os.path.realpath(token)
        name = os.path.basename(real)
        if stem(name) in FLOOR_STEMS:
            terms.add(stem(name))
            continue
        if digests is None:
            digests = floor_digests()
        value = digest(real)
        if value in digests:
            terms.add(stem(digests[value]))
    return terms


def mentioned_floors(command):
    if not isinstance(command, str):
        return set()
    texts = readings(command)
    found = {term for term in FLOOR_MENTIONS if any(term.lower() in text for text in texts)}
    return found | resolved_floors(command)


def unclassified_guards(command):
    if not isinstance(command, str):
        return set()
    names = set()
    for token in path_tokens(command):
        for name in {os.path.basename(token), os.path.basename(os.path.realpath(token))}:
            if "guard" in name.lower() and stem(name) not in CLASSIFIED_STEMS:
                names.add(name)
    return names


def accepted_forms(command):
    return (command,) + ACCEPTED_FORMS.get(command, ())


HOOKS_OFF_KEYS = ("disableAllHooks", "allowManagedHooksOnly")
HOOKS_OFF_ENV = ("CLAUDE_CODE_SIMPLE", "CLAUDE_CODE_SAFE_MODE")


def hooks_off(settings):
    if not isinstance(settings, dict):
        return []
    found = [key for key in HOOKS_OFF_KEYS if settings.get(key) not in (None, False)]
    env = settings.get("env")
    if isinstance(env, dict):
        found += [
            f"env.{name}" for name in HOOKS_OFF_ENV if str(env.get(name, "")).strip().lower() not in {"", "0", "false"}
        ]
    return found


FAIL_OPEN = re.compile(
    r'if \[ -r "(?P<path>[^"]+)" \]; then (?P<run>[^;]+); '
    r"else printf '%s\\n' \"hook warning: missing (?P=path); continuing\" >&2; fi # hook missing: fail open"
)
INTERPRETERS = tuple(
    path
    for path in ("/usr/bin/python3", "/opt/homebrew/bin/python3", "/opt/homebrew/bin/node", "/bin/bash")
    if os.path.isfile(path)
)
ENV = ("env", "/usr/bin/env")
UNSAFE = set(";&|<>()`$*?[]{}!\\'\"\n")


def unwrapped(command):
    match = FAIL_OPEN.fullmatch(command) if isinstance(command, str) else None
    return match["run"] if match else None


def script_path(command):
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    home = os.path.expanduser("~")
    words = [re.sub(r"^(?:\$HOME|\$\{HOME\}|~)(?=/)", lambda _: home, word) for word in words]
    if not words or any(set(word) & UNSAFE for word in words):
        return None
    if words[0] in ENV:
        words = words[1:]
    if len(words) != 2 or words[0] not in INTERPRETERS or not words[1].startswith("/"):
        return None
    real = os.path.realpath(words[1])
    return real if os.path.isfile(real) else None


SYSTEM = frozenset((*INTERPRETERS, *ENV[1:], "/bin/sh", "/bin/zsh", "/usr/bin/bash", "/dev/null"))
FAIL_OPEN_MARKS = ("hook missing: fail open", "hook warning: missing")


def provenance(command, scripts):
    for token in path_tokens(command):
        if token in SYSTEM or os.path.isdir(token) and not os.path.islink(token):
            continue
        real = os.path.realpath(token)
        if os.path.islink(token) or real not in scripts or digest(real) not in scripts[real]:
            return False
    return True


def non_floor(command, scripts, exact=frozenset()):
    if not isinstance(command, str) or mentioned_floors(command):
        return False
    inner = unwrapped(command)
    if inner is not None:
        return not any(mark in inner for mark in FAIL_OPEN_MARKS) and non_floor(inner, scripts, exact)
    if any(mark in command for mark in FAIL_OPEN_MARKS) or not provenance(command, scripts):
        return False
    if command in exact:
        return True
    real = script_path(command)
    return bool(real) and real not in map(os.path.realpath, SYSTEM) and digest(real) not in floor_digests()
