from __future__ import annotations

import re
from pathlib import Path

from policy_layout import CODEX_END, CODEX_START, END, ROUTING_HEADINGS, START  # type: ignore[missing-import]


def read_text(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def validate_markers(text: str, path: Path) -> None:
    starts = text.count(START)
    ends = text.count(END)
    if starts != ends or starts > 1:
        raise ValueError(f"malformed or duplicate managed routing markers in {path}")
    if starts and text.index(START) > text.index(END):
        raise ValueError(f"reversed managed routing markers in {path}")


def h2_span(text: str, headings: tuple[str, ...], path: Path) -> tuple[int, int] | None:
    matches: list[re.Match[str]] = []
    for heading in headings:
        pattern = re.compile(rf"(?m)^##[ \t]+{re.escape(heading)}[ \t]*$")
        matches.extend(pattern.finditer(text))
    if len(matches) > 1:
        raise ValueError(f"multiple legacy routing sections in {path}")
    if not matches:
        return None
    start = matches[0].start()
    next_h2 = re.search(r"(?m)^##[ \t]+", text[matches[0].end() :])
    end = matches[0].end() + (next_h2.start() if next_h2 else len(text[matches[0].end() :]))
    return start, end


def install_adapter(text: str, template: str, path: Path) -> str:
    validate_markers(text, path)
    if START in text:
        start = text.index(START)
        end = text.index(END, start) + len(END)
        updated = text[:start] + template.strip() + text[end:]
    else:
        span = h2_span(text, ROUTING_HEADINGS, path)
        if span:
            updated = text[: span[0]] + template.strip() + "\n\n" + text[span[1] :].lstrip("\n")
        elif text:
            updated = text.rstrip() + "\n\n" + template.strip() + "\n"
        else:
            updated = template.strip() + "\n"
    return updated if updated.endswith("\n") else updated + "\n"


def install_codex_doc(text: str, body: str, path: Path) -> str:
    text = uninstall_adapter(text, path)
    if text.count(CODEX_START) > 1 or text.count(CODEX_START) != text.count(CODEX_END):
        raise ValueError(f"malformed or duplicate Codex markers in {path}")
    block = CODEX_START + "\n" + body.strip() + "\n" + CODEX_END
    if CODEX_START in text:
        start = text.index(CODEX_START)
        end = text.index(CODEX_END, start) + len(CODEX_END)
        updated = text[:start] + block + text[end:]
    elif not text.strip() or text.strip() == body.strip():
        updated = block
    else:
        updated = text.rstrip() + "\n\n" + block
    return updated.rstrip() + "\n"


def uninstall_adapter(text: str, path: Path) -> str:
    validate_markers(text, path)
    if START not in text:
        span = h2_span(text, ROUTING_HEADINGS, path)
        if span:
            updated = text[: span[0]].rstrip() + "\n\n" + text[span[1] :].lstrip("\n")
            return updated.rstrip() + "\n" if updated.strip() else ""
        return text
    start = text.index(START)
    end = text.index(END, start) + len(END)
    updated = text[:start].rstrip() + "\n\n" + text[end:].lstrip("\n")
    return updated.rstrip() + "\n" if updated.strip() else ""
