from __future__ import annotations

import re

WORD = r"(?:\$HOME|\$\{HOME\}|~)?[A-Za-z0-9_./@:+,=-]+"
PLAIN = re.compile(rf'"{WORD}"|{WORD}')
INTERPRETERS = frozenset({"python3", "/usr/bin/python3", "python", "bash", "/bin/bash", "sh", "/bin/sh"})


def sole_invocation(command: str) -> str | None:
    words = command.split()
    if not words or not all(PLAIN.fullmatch(word) for word in words):
        return None
    if words[0] in INTERPRETERS:
        words = words[1:]
        while words and words[0].startswith("-"):
            words = words[1:]
    return words[0].strip('"') if words else None
