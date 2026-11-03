import re

LEXEME = re.compile(
    r"(?<![\w$])[Ee]'(?:[^'\\]|\\[\s\S]|'')*'"
    r"|'(?:[^']|'')*'"
    r"|\"(?:[^\"]|\"\")*\""
    r"|(?<![\w$])(\$(?:[A-Za-z_]\w*)?\$)[\s\S]*?\1"
    r"|/\*[\s\S]*?\*/"
    r"|--[^\n]*"
)
DELETE_FROM = re.compile(r"\bDELETE\s+FROM\b[^;]*", re.I)
WHERE = re.compile(r"\bWHERE\b", re.I)


def code(sql):
    return LEXEME.sub(lambda m: " " if m[0][:1] in "/-" else '""' if m[0][:1] == '"' else "''", sql)


def deletes_all(sql):
    return any(not WHERE.search(m[0]) for m in DELETE_FROM.finditer(code(sql)))
