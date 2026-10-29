from __future__ import annotations

import re
import threading
from pathlib import Path

SHARED_ROOTS = frozenset(
    {"bin", "dev", "etc", "lib", "lib64", "opt", "proc", "sbin", "sys", "usr", "System", "Library", "Applications"}
)
SYNCED_HOME = tuple(Path.home() / name for name in (".claude", ".agents", ".agent-lb", ".codex", ".local/bin"))
QUOTED = re.compile(r"\"(~?/[^\"\n]+)\"|`(~?/[^`\n]+)`|'(~?/[^'\n]+)'")
BARE = re.compile(r"(?<![\w/~.:-])(~?/[^\s`'\"<>()\[\]{},;|]+)")
TRAILING = re.compile(r"(?::\d+(?::\d+)?)?[.,:;!?]*$")
PROBE_DEADLINE_S = 2.0
MAX_TOKENS = 200


def local_candidate(text: str) -> bool:
    path = Path(text).expanduser()
    if len(path.parts) < 2 or path.parts[1] in SHARED_ROOTS:
        return False
    return not any(path == home or home in path.parents for home in SYNCED_HOME)


def candidates(brief: str) -> tuple[list[str], bool]:
    seen: list[str] = []
    quoted = [next(g for g in m.groups() if g) for m in QUOTED.finditer(brief)]
    for token in quoted + BARE.findall(brief):
        for text in (token, TRAILING.sub("", token)):
            if text and text not in seen and local_candidate(text):
                if len(seen) == MAX_TOKENS:
                    return seen, True
                seen.append(text)
    return seen, False


def caller_host_paths(brief: str, root: Path) -> list[str]:
    found: list[str] = []
    named: set[Path] = set()

    tokens, capped = candidates(brief)

    def probe() -> None:
        for text in tokens:
            path = Path(text).expanduser()
            try:
                resolved = path.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if resolved == root or root in resolved.parents or resolved in named:
                continue
            named.add(resolved)
            found.append(text)

    worker = threading.Thread(target=probe, daemon=True)
    worker.start()
    worker.join(PROBE_DEADLINE_S)
    if worker.is_alive():
        return [*found, f"(path probe exceeded {PROBE_DEADLINE_S:g} s)"]
    if capped:
        return [*found, f"(more than {MAX_TOKENS} path tokens)"]
    return found
