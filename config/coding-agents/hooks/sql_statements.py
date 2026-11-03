import re

IDENT = r"\w$\x80-\U0010FFFF"
LEXEME = re.compile(
    r"(?<![" + IDENT + r"])[Ee]'(?:[^'\\]|\\[\s\S]|'')*'"
    r"|'(?:[^']|'')*'"
    r"|\"(?:[^\"]|\"\")*\""
    r"|(?<![" + IDENT + r"])(\$(?:[A-Za-z_\x80-\U0010FFFF][\w\x80-\U0010FFFF]*)?\$)[\s\S]*?\1"
    r"|--[^\n]*"
    r"|/\*"
    r"|\\\\"
    r"|\\[^\n\\]*"
)
COMMENT_MARK = re.compile(r"/\*|\*/")
DELETE_FROM = re.compile(r"(?<![" + IDENT + r"])DELETE\s+FROM(?![" + IDENT + r"])[^;]*", re.I)
WHERE = re.compile(r"(?<![" + IDENT + r"])WHERE(?![" + IDENT + r"])", re.I)


def block_end(piece, start):
    depth, i = 1, start
    while depth:
        mark = COMMENT_MARK.search(piece, i)
        if mark is None:
            return None
        depth += 1 if mark[0] == "/*" else -1
        i = mark.end()
    return i


def lexed(piece):
    out, i = [], 0
    while (m := LEXEME.search(piece, i)) is not None:
        out.append(piece[i : m.start()])
        i = m.end()
        if m[0] == "/*":
            end = block_end(piece, i)
            out.append(" ")
            i = i if end is None else end
        else:
            out.append(" " if m[0][:1] == "-" else ";" if m[0][:1] == "\\" else '""' if m[0][:1] == '"' else "''")
    return "".join(out) + piece[i:]


def code(pieces):
    return ";\n".join(map(lexed, pieces))


def deletes_all(pieces):
    return any(not WHERE.search(m[0]) for m in DELETE_FROM.finditer(code(pieces)))
