from __future__ import annotations

from collections.abc import Callable

SEPARATORS = ("&&", "||", ";", "\n")
UNSPLITTABLE = set("(){}`")


def command_list(command: str) -> list[tuple[str, str]] | None:
    parts: list[tuple[str, str]] = []
    buf: list[str] = []
    quote = None
    i = 0
    while i < len(command):
        ch = command[i]
        if quote:
            buf.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < len(command):
                buf.append(command[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch == "\\" and i + 1 < len(command):
            buf += [ch, command[i + 1]]
            i += 2
            continue
        sep = next((s for s in SEPARATORS if command.startswith(s, i)), None)
        if sep:
            parts.append(("".join(buf), sep))
            buf = []
            i += len(sep)
            continue
        if ch in UNSPLITTABLE or command.startswith("$(", i):
            return None
        if ch == "&" and not (command[i - 1 : i] == ">" or command.startswith("&>", i)):
            return None
        if ch in "'\"":
            quote = ch
        buf.append(ch)
        i += 1
    if quote:
        return None
    parts.append(("".join(buf), ""))
    return parts


def without_segments(command: str, retired: Callable[[str], bool]) -> str | None:
    if not retired(command):
        return command
    parts = command_list(command)
    if parts is None:
        return command
    kept = [(segment, sep) for segment, sep in parts if segment.strip() and not retired(segment)]
    if not kept:
        return None
    return "".join(
        segment + (sep if index < len(kept) - 1 else "") for index, (segment, sep) in enumerate(kept)
    ).strip()
