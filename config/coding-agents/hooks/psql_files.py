import os
import re

DEPTH = 16
META = re.compile(
    r"\\(?:(cd)(?=\s|\\|$)[ \t]*('[^']*'|\"[^\"]*\"|[^\s\\]+)?"
    r"|(include_relative|include|ir|i)\s+('[^']*'|\"[^\"]*\"|\S+))"
)


def file_text(path):
    if not os.path.isfile(path):
        return None
    try:
        if os.path.getsize(path) >= 1_000_000:
            raise OSError("larger than limit")
        with open(path) as f:
            return f.read(1_000_000)
    except (OSError, UnicodeError):
        return None


def reader(cwds, unread, loaded):
    seen = set()

    def known():
        return sorted(c for c in cwds if c)

    def places(path, bases):
        path = os.path.expanduser(path)
        return [path] if os.path.isabs(path) else [os.path.join(base, path) for base in bases]

    def load(place, depth, stdin):
        here = [os.path.dirname(place)] + (known() if stdin else [])
        key = (os.path.realpath(place), tuple(map(os.path.realpath, here)), frozenset(cwds))
        text = file_text(place)
        if text is None:
            return None
        loaded.add(os.path.realpath(place))
        if key in seen:
            return [text]
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
            if name in ("-", "/dev/stdin") or name.startswith(("/dev/", "/proc/")):
                unread.append(name)
                continue
            if depth > DEPTH:
                unread.append("%s (psql includes nested past %d)" % (name, DEPTH))
                continue
            bases = here if m[3] in ("ir", "include_relative") else known()
            got = [t for t in (load(p, depth, False) for p in dict.fromkeys(places(name, bases))) if t is not None]
            if not got:
                unread.append(name)
            pieces += [piece for found in got for piece in found]
        return pieces

    def read(path, stdin=False):
        got = [t for t in (load(p, 0, stdin) for p in dict.fromkeys(places(path, known()))) if t is not None]
        if not got:
            unread.append(path)
        return [piece for found in got for piece in found]

    def inline(text):
        return [text] + included(text, known(), 1)

    return read, inline
