import codecs
import functools
import hashlib
import os
import re
import shlex
from pathlib import Path

FLOOR_HOOKS = {
    "branch-guard.py": "git: no branch switch in a shared checkout",
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
}
NON_FLOOR_GUARDS = {
    "desktop-guard": "routing rule (computer use off his desktop), not a kept floor",
    "wide-scan-guard.sh": "house-tools rule against wide greps, not a kept floor",
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


@functools.lru_cache(maxsize=4096)
def file_digest(path, mtime, size):
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None


def digest(path):
    try:
        info = os.stat(path)
    except OSError:
        return None
    if not os.path.isfile(path) or info.st_size > MAX_DIGEST_BYTES:
        return None
    return file_digest(path, info.st_mtime_ns, info.st_size)


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
