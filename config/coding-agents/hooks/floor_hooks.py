import re

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


def mentioned_floors(command):
    return {term for term in FLOOR_MENTIONS if term in command} if isinstance(command, str) else set()


def accepted_forms(command):
    return (command,) + ACCEPTED_FORMS.get(command, ())
