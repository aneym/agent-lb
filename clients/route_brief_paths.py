from __future__ import annotations

import re
import threading
from pathlib import Path

SHARED_ROOTS = frozenset(
    {"bin", "dev", "etc", "lib", "lib64", "opt", "proc", "sbin", "sys", "usr", "System", "Library", "Applications"}
)
SYNCED_HOME = tuple(Path.home() / name for name in (".claude", ".agents", ".agent-lb", ".codex", ".local/bin"))
QUOTED = re.compile(r"""(["'`])(~?/[^"'`\n]+)\1""")
BARE = re.compile(r"(?<![\w/~.:-])(~?/[^\s`'\"<>()\[\]{},;|]+)")
TRAILING = re.compile(r"(?::\d+(?::\d+)?)?[.,:;!?]*$")
PROBE_DEADLINE_S = 2.0
MAX_TOKENS = 200


def candidates(brief: str) -> list[str]:
    seen: list[str] = []
    for token in [m.group(2) for m in QUOTED.finditer(brief)] + BARE.findall(brief):
        for text in (token, TRAILING.sub("", token)):
            if text and text not in seen:
                seen.append(text)
    return seen[:MAX_TOKENS]


def caller_host_paths(brief: str, root: Path) -> list[str]:
    found: list[str] = []
    named: set[Path] = set()

    def probe() -> None:
        for text in candidates(brief):
            path = Path(text).expanduser()
            if len(path.parts) < 2 or path.parts[1] in SHARED_ROOTS:
                continue
            if any(path == home or home in path.parents for home in SYNCED_HOME):
                continue
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
    return found
