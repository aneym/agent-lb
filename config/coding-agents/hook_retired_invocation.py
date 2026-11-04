from __future__ import annotations

import re
import shlex
from collections.abc import Iterable

PLAIN = re.compile(r"[A-Za-z0-9_./=:+-]+(?: [A-Za-z0-9_./=:+-]+)*")


def retired_invocation(command: str, names: Iterable[str]) -> bool:
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    if len(words) == 2 and words[0].rsplit("/", 1)[-1] in {"python3", "bash", "sh"}:
        return words[1].rsplit("/", 1)[-1] in set(names) and words[1].startswith(
            ("$HOME/.claude/hooks/", "${HOME}/.claude/hooks/", "~/.claude/hooks/")
        )
    return bool(PLAIN.fullmatch(command)) and command.split(" ")[0].rsplit("/", 1)[-1] in set(names)
