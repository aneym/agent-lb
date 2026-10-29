import codecs
import re
import shlex

FLOOR_HOOKS = (
    "branch-guard.py",
    "rm-dynamic-deny",
    "stash-guard",
    "railway-vars-guard.sh",
    "link-cli-guard.sh",
    "plutil-guard.sh",
    "kill-guard",
    "agent-lb-bootout-guard.sh",
    "dangerous-command-guard.sh",
    "no-chrome-guard.sh",
    "host-credential-guard.py",
    "gh-write-guard.py",
    "kill-scope-guard.py",
    "merge-gate-guard.py",
)
FLOOR_TEXT = "BLOCKED: Dangerous command"
INLINE_FLOOR = "dangerous-command"
FLOOR_WORD = re.compile(r"(?:^|[/\s\"'])(%s)(?=$|[\s\"';|&)])" % "|".join(re.escape(name) for name in FLOOR_HOOKS))
FLOOR_MENTIONS = tuple(name.rsplit(".", 1)[0] if name.endswith((".py", ".sh")) else name for name in FLOOR_HOOKS) + (
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


def readings(command):
    decoded = ansi_c(command)
    texts = [command, decoded]
    texts += [text.replace("\\", "").replace("'", "").replace('"', "") for text in texts]
    for text in (command, decoded):
        try:
            texts += shlex.split(text)
        except ValueError:
            pass
    return [text.lower() for text in texts]


def mentioned_floors(command):
    if not isinstance(command, str):
        return set()
    texts = readings(command)
    return {term for term in FLOOR_MENTIONS if any(term.lower() in text for text in texts)}


def accepted_forms(command):
    return (command,) + ACCEPTED_FORMS.get(command, ())
