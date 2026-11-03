import re

ESCAPE = re.compile(r"\\(x[0-9A-Fa-f]{1,2}|0?[0-7]{1,3}|[\s\S])")
SPEC = re.compile(r"%%|%[-+ #0-9.*]*[A-Za-z]")
NAMED = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "e": "\x1b", "f": "\f", "v": "\v"}


def unescape(text):
    def one(m):
        e = m[1]
        if e[0] == "x" and len(e) > 1:
            return chr(int(e[1:], 16))
        if e[0] in "01234567":
            return chr(int(e, 8) & 0xFF)
        return NAMED.get(e, e)

    return ESCAPE.sub(one, text)


def printf(args):
    fmt, rest, out = args[0], list(args[1:]), []
    while True:
        used = 0

        def spec(m):
            nonlocal used
            if m[0] == "%%":
                return "%"
            used += 1
            return rest.pop(0) if rest else ""

        out.append(SPEC.sub(spec, fmt))
        if not rest or not used:
            return "".join(out)


def texts(name, args):
    if name == "echo":
        while args and re.fullmatch(r"-[neE]+", args[0]):
            args = args[1:]
        raw = " ".join(args)
        return [raw, unescape(raw)]
    if not args:
        return []
    formatted = printf(args)
    return [" ".join(args), formatted, unescape(formatted)]
