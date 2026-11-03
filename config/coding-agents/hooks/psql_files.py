import os
import re
import sys

DEPTH = 16
META = re.compile(
    r"\\(?:(cd)(?=\s|\\|$)[ \t]*('[^']*'|\"[^\"]*\"|[^\s\\]+)?"
    r"|(include_relative|include|ir|i)\s+('[^']*'|\"[^\"]*\"|\S+))"
)


def file_text(path):
    if not os.path.exists(path):
        return None
    try:
        if os.path.getsize(path) >= 1_000_000:
            raise OSError("larger than limit")
        with open(path) as f:
            return f.read(1_000_000)
    except (OSError, UnicodeError):
        sys.stderr.write("dangerous-command-guard: SQL input unreadable or >=1 MB; passing without inspection.\n")
        return None


def reader(cwds, start, unread):
    seen = set()

    def known():
        return sorted(c for c in cwds if c)

    def places(path, bases):
        path = os.path.expanduser(path)
        return [path] if os.path.isabs(path) else [os.path.join(base, path) for base in bases]

    def load(place, depth, stdin):
        here = [os.path.dirname(place)] + (known() if stdin else [])
        key = (os.path.realpath(place), tuple(map(os.path.realpath, here)), frozenset(cwds))
        if key in seen:
            return []
        text = file_text(place)
        if text is None:
            return None
        seen.add(key)
        return [text] + included(text, here, depth + 1)

    def included(text, here, depth):
        pieces = []
        for m in META.finditer(text):
            if m[1]:
                target = os.path.expanduser((m[2] or "~").strip("'\""))
                dirs = {os.path.realpath(os.path.join(c, target)) for c in known()}
                dirs = {d for d in dirs if os.path.isdir(d)}
                if not dirs:
                    unread.append("\\cd " + target)
                cwds.update(dirs)
                continue
            name = m[4].strip("'\"")
            if depth > DEPTH:
                unread.append("%s (psql includes nested past %d)" % (name, DEPTH))
                continue
            bases = here if m[3] in ("ir", "include_relative") else known()
            got = [t for t in (load(p, depth, False) for p in dict.fromkeys(places(name, bases))) if t is not None]
            if not got and name != "-":
                unread.append(name)
            pieces += [piece for found in got for piece in found]
        return pieces

    def read(path, stdin=False):
        if path in ("", "-"):
            return []
        got = [t for t in (load(p, 0, stdin) for p in dict.fromkeys(places(path, known()))) if t is not None]
        if not got and None in cwds and not os.path.isabs(path) and not os.path.exists(os.path.join(start, path)):
            unread.append(path)
        return [piece for found in got for piece in found]

    def inline(text):
        return [text] + included(text, known(), 1)

    return read, inline
