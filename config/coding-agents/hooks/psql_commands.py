import re
import shlex

HEREDOC = re.compile(r"<<(-?)[ \t]*(['\"]?)([^\s'\"<>;|&()\x05]+)\2")
SHELL_LINE = re.compile(r"\s*(?:bash|sh|zsh)\b")
OPERATORS = re.compile(r"&>>|<<<|\|\||&&|;;&?|;&|\|&|<<|<>|<&|<\(|>>|>&|>\||>\(|&>|[;|&<>()]")
MASKED = {"\x02": "$", "\x03": "`", "\x04": "$"}
DOC = "\x05heredoc:"


def restore(text):
    for mark, char in MASKED.items():
        text = text.replace(mark, char)
    return text


def scan(line, state):
    out, docs, i = [], [], 0
    while i < len(line):
        c = line[i]
        if state == "'":
            out.append({"$": "\x02", "`": "\x03"}.get(c, c))
            state = "" if c == "'" else state
        elif c == "\\" and line[i + 1 : i + 2] in ("$", "`"):
            out.append("\x02" if line[i + 1] == "$" else "\x03")
            i += 1
        elif c == "\\":
            out.append(line[i : i + 2])
            i += 1
        elif state == '"':
            out.append(c)
            state = "" if c == '"' else state
        elif c == "$" and line[i + 1 : i + 2] in ("'", '"'):
            out.append("\x04")
        elif c in "'\"":
            out.append(c)
            state = c
        elif c == "\n":
            out.append(";")
        elif c == "#" and (i == 0 or line[i - 1] in " \t;|&()"):
            out.append(";" if line.endswith("\n") else "")
            break
        elif line.startswith("<<<", i):
            out.append("<<<")
            i += 2
        elif line.startswith("<<", i) and (m := HEREDOC.match(line, i)):
            out.append("<< %sL%d\x06 " % (DOC, len(docs)))
            docs.append(m)
            i = m.end() - 1
        else:
            out.append(c)
        i += 1
    return "".join(out), docs, state


def operators(token):
    if token and set(token) <= set(";|&<>()"):
        return OPERATORS.findall(token)
    return [token]


def commands(text, bodies, feeds):
    lines = text.splitlines(keepends=True)
    clean, state, i = [], "", 0
    while i < len(lines):
        line = lines[i]
        i += 1
        while line.endswith("\\\n") and not line.endswith("\\\\\n") and i < len(lines):
            line = line[:-2] + lines[i]
            i += 1
        masked, docs, state = scan(line, state)
        for j, m in enumerate(docs):
            body, name = [], m[3].replace("\\", "")
            while i < len(lines) and (lines[i].lstrip("\t") if m[1] else lines[i]).rstrip("\n") != name:
                body.append(lines[i])
                i += 1
            i += 1
            masked = masked.replace("%sL%d\x06" % (DOC, j), DOC + str(len(bodies)))
            bodies.append(("".join(body), bool(m[2]) or "\\" in m[3]))
            if SHELL_LINE.match(line):
                yield from commands("".join(body), bodies, feeds)
        clean.append(masked)
    lexer = shlex.shlex("".join(clean), posix=True, punctuation_chars=";|&<>()")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    segment = []
    for token in (op for raw in lexer for op in operators(raw)):
        if token[:1] == "<" or token == ">(" or "|" in token.replace("||", "").replace(">|", ""):
            feeds.append(token)
        if token in ("<(", ">("):
            yield segment + [token]
            segment = []
        elif token and set(token) <= set(";|&()"):
            if segment:
                yield segment
                segment = []
        else:
            segment.append(token)
    if segment:
        yield segment


def body(word, bodies):
    tail = word[len(DOC) :]
    if word.startswith(DOC) and tail.isdigit() and int(tail) < len(bodies):
        return bodies[int(tail)]
    return None
