from __future__ import annotations

import re
from collections.abc import Iterable

PLAIN = re.compile(r"[A-Za-z0-9_./=:+-]+(?: [A-Za-z0-9_./=:+-]+)*")


def retired_invocation(command: str, names: Iterable[str]) -> bool:
    return bool(PLAIN.fullmatch(command)) and command.split(" ")[0].rsplit("/", 1)[-1] in set(names)
