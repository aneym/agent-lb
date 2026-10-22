
import re
import shlex


def strip_heredocs(text):
    kept, pending, bodies = [], [], []
    quote, stack, i, line_start = None, [], 0, 0
    while i < len(text):
        char = text[i]
        if char == '\\' and quote != "'":
            kept.append(text[i:i + 2])
            i += 2
            continue
        if quote == "'":
            if char == "'":
                quote = None
        elif text.startswith('$(', i):
            stack.append(quote)
            quote = None
            kept.append('$(')
            i += 2
            continue
        elif quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == '(' and stack:
            stack.append(None)
        elif char == ')' and stack:
            quote = stack.pop()
        elif char == '#' and (i == 0 or text[i - 1] in ' \t\r\n;&|()'):
            end = text.find('\n', i)
            if end == -1:
                kept.append(text[i:])
                break
            kept.append(text[i:end])
            i = end
            continue
        elif text.startswith('<<', i) and not text.startswith('<<<', i):
            match = re.match(r'''<<(-?)[ \t]*((?:[^\s;&|<>()'"\\]|\\.|'[^']*'|"[^"]*")+)''', text[i:])
            if not match:
                raise ValueError('missing heredoc delimiter')
            tabs, spelling = match.groups()
            delimiter = ''.join(shlex.split(spelling))
            quoted = any(c in spelling for c in "\\\"'")
            pending.append((delimiter, bool(tabs), quoted))
            kept.append('< /dev/null')
            i += match.end()
            continue
        elif text.startswith('<<<', i):
            kept.append('<<<')
            i += 3
            continue
        kept.append(char)
        i += 1
        if char == '\n':
            header = text[line_start:i]
            executable = re.search(r'(?:^|[\s;|(&])(?:/[^\s;|(&]+/)?(?:bash|sh|zsh|dash|ksh)(?=[\s;<]|$)', header) is not None
            for delimiter, tabs, quoted in pending:
                body = []
                while i < len(text):
                    end = text.find('\n', i)
                    end = len(text) if end == -1 else end + 1
                    line = text[i:end]
                    i = end
                    candidate = line.rstrip('\r\n')
                    if tabs:
                        candidate = candidate.lstrip('\t')
                    if candidate == delimiter:
                        content = ''.join(body)
                        bodies.extend([content] if executable else substitutions(content, heredoc=True) if not quoted else [])
                        break
                    body.append(line.lstrip('\t') if tabs else line)
                else:
                    raise ValueError('unfinished heredoc')
            pending = []
            line_start = i
    if pending:
        raise ValueError('unfinished heredoc')
    return ''.join(kept), bodies
def join_continuations(text):
    kept, quote, i = [], None, 0
    while i < len(text):
        char = text[i]
        if char == '\\' and quote != "'":
            if text[i:i + 2] == '\\\n':
                i += 2
                continue
            kept.append(text[i:i + 2])
            i += 2
            continue
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        kept.append(char)
        i += 1
    return ''.join(kept)
def shell_tokens(text):
    cleaned = []
    quote = None
    escaped = False
    boundary = True
    comment = False
    for char in text:
        if comment:
            if char != '\n':
                continue
            comment = False
        if escaped:
            escaped = False
            boundary = False
        elif char == '\\' and quote != "'":
            escaped = True
            boundary = False
        elif quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
            boundary = False
        elif char == '#' and boundary:
            comment = True
            continue
        else:
            boundary = char in ' \t\r\n;&|()'
        cleaned.append(char)
    lexer = shlex.shlex(''.join(cleaned), posix=True, punctuation_chars=';&|()<>\n')
    lexer.whitespace = ' \t\r'
    lexer.whitespace_split = True
    lexer.commenters = ''
    return list(lexer)
def substitutions(text, heredoc=False):
    bodies = []
    quote = None
    boundary = True
    i = 0
    while i < len(text):
        char = text[i]
        if not heredoc and quote is None and char == '#' and boundary:
            end = text.find('\n', i)
            if end == -1:
                break
            i = end + 1
            continue
        if char == '\\' and quote != "'":
            i += 2
            boundary = False
            continue
        if quote == "'":
            if char == "'":
                quote = None
        elif char == "'" and quote is None and not heredoc:
            quote = "'"
        elif char == '"' and not heredoc:
            quote = None if quote == '"' else '"'
        elif char == '`' or text.startswith('$(', i):
            backtick = char == '`'
            start = i + (1 if backtick else 2)
            j, level, inner_quote = start, 1, None
            while j < len(text):
                c = text[j]
                if c == '\\' and inner_quote != "'":
                    j += 2
                    continue
                if backtick and c == '`':
                    break
                if inner_quote:
                    if c == inner_quote:
                        inner_quote = None
                elif c in "\"'":
                    inner_quote = c
                elif not backtick:
                    if c == '(':
                        level += 1
                    elif c == ')':
                        level -= 1
                        if level == 0:
                            break
                j += 1
            if j == len(text):
                raise ValueError('unfinished command substitution')
            bodies.append(text[start:j])
            i = j
        boundary = quote is None and char in ' \t\r\n;&|()'
        i += 1
    return bodies
