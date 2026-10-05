"""Token-aware scanning of tracked text and added diff lines."""

import re
import subprocess
from pathlib import Path

from .classify import classify

TOKEN = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+[0-9]*|[A-Z]+[0-9]*|[0-9]+[a-zA-Z]*")


def tokens(text):
    return [(m.group().lower(), m.start()) for m in TOKEN.finditer(text)]


def matches(text, form):
    haystack = tokens(text)
    needle = [word for word, _ in tokens(form)]
    if not needle:
        return
    for i in range(len(haystack) - len(needle) + 1):
        if [word for word, _ in haystack[i : i + len(needle)]] == needle:
            yield haystack[i][1]


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL)


def added_lines(repo, base):
    result = {}
    path = None
    number = 0
    for line in (
        git(repo, "diff", "--no-ext-diff", "--no-color", "--unified=0", f"{base}...HEAD")
        .decode("utf-8", "replace")
        .splitlines()
    ):
        if line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("@@"):
            number = int(re.search(r"\+(\d+)", line).group(1))
        elif line.startswith("+") and path:
            result.setdefault(path, []).append((number, line[1:]))
            number += 1
        elif line.startswith(" "):
            number += 1
    return result


def scan(repo, terms, allowlist, paths=(), diff=None, filenames=False):
    hits = []
    added = added_lines(repo, diff) if diff else None
    for raw in git(repo, "ls-files", "-z").split(b"\0"):
        if not raw:
            continue
        path = raw.decode("utf-8", "surrogateescape")
        if paths and not any(path == p or path.startswith(p.rstrip("/") + "/") for p in paths):
            continue
        target = Path(repo) / path
        if filenames:
            for term in terms:
                found = next(((form, col) for form in term["forms"] for col in matches(path, form)), None)
                if found:
                    form, col = found
                    hits.append(hit(path, 0, col, form, term, path, allowlist, True))
        try:
            if not target.is_file() or target.is_symlink() or target.stat().st_size > 1024 * 1024:
                continue
            content = target.read_bytes()
            if b"\0" in content:
                continue
            text = content.decode("utf-8")
        except (OSError, UnicodeError):
            continue
        lines = added.get(path, []) if added is not None else enumerate(text.splitlines(), 1)
        for number, line in lines:
            for term in terms:
                seen = set()
                for form in term["forms"]:
                    for col in matches(line, form):
                        if col not in seen:
                            hits.append(hit(path, number, col, form, term, line, allowlist))
                            seen.add(col)
    return hits


def hit(path, number, col, form, term, text, allowlist, filename=False):
    return {
        "path": path,
        "line_no": number,
        "col": col + 1,
        "form": form,
        "old": term["old"],
        "new": term["new"],
        "text": text[:200],
        "class": classify(path, text, col, term, allowlist, filename),
    }
