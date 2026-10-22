#!/bin/bash
set -euo pipefail
refuse() {
  echo "BLOCKED (floor): wide-scan-guard $1, so it fails closed." >&2
  exit 2
}
allow() {
  if [ -n "${HOOK_FLOOR_RECEIPT:-}" ]; then printf 'floor-ok %s\n' wide-scan-guard.sh; fi
  exit 0
}
warn() {
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"%s"}}\n' "wide-scan-guard (warn only, not blocked): $1"
  allow
}
INPUT=$(cat) || refuse "could not read the hook input (cat exit $?)"
[ -n "$INPUT" ] || refuse "read an empty hook input"
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty') || refuse "could not read the hook input (jq exit $?)"
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty') || refuse "could not read the hook input (jq exit $?)"
[ -n "$CMD" ] || allow
PYTHONPATH="$(python3 -c 'import os, sys; print(os.path.dirname(os.path.realpath(sys.argv[1])))' "${BASH_SOURCE[0]}")${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONPATH
SCAN=0
SCAN_COMMAND="$CMD" SCAN_CWD="$CWD" python3 - <<'PY' || SCAN=$?
import os
import shlex
import re
import posixpath




def strip_heredocs(text):
    kept, pending, bodies = [], [], []
    quote, stack, i, line_start = None, [], 0, 0
    while i < len(text):
        char = text[i]
        if char == "\\" and quote != "'":
            kept.append(text[i : i + 2])
            i += 2
            continue
        if quote == "'":
            if char == "'":
                quote = None
        elif text.startswith("$(", i):
            stack.append(quote)
            quote = None
            kept.append("$(")
            i += 2
            continue
        elif quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "(" and stack:
            stack.append(None)
        elif char == ")" and stack:
            quote = stack.pop()
        elif char == "#" and (i == 0 or text[i - 1] in " \t\r\n;&|()"):
            end = text.find("\n", i)
            if end == -1:
                kept.append(text[i:])
                break
            kept.append(text[i:end])
            i = end
            continue
        elif text.startswith("<<", i) and not text.startswith("<<<", i):
            match = re.match(r"""<<(-?)[ \t]*((?:[^\s;&|<>()'"\\]|\\.|'[^']*'|"[^"]*")+)""", text[i:])
            if not match:
                raise ValueError("missing heredoc delimiter")
            tabs, spelling = match.groups()
            delimiter = "".join(shlex.split(spelling))
            quoted = any(c in spelling for c in "\\\"'")
            pending.append((delimiter, bool(tabs), quoted))
            kept.append("< /dev/null")
            i += match.end()
            continue
        elif text.startswith("<<<", i):
            kept.append("<<<")
            i += 3
            continue
        kept.append(char)
        i += 1
        if char == "\n":
            header = text[line_start:i]
            executable = (
                re.search(r"(?:^|[\s;|(&])(?:/[^\s;|(&]+/)?(?:bash|sh|zsh|dash|ksh)(?=[\s;<]|$)", header) is not None
            )
            for delimiter, tabs, quoted in pending:
                body = []
                while i < len(text):
                    end = text.find("\n", i)
                    end = len(text) if end == -1 else end + 1
                    line = text[i:end]
                    i = end
                    candidate = line.rstrip("\r\n")
                    if tabs:
                        candidate = candidate.lstrip("\t")
                    if candidate == delimiter:
                        content = "".join(body)
                        bodies.extend(
                            [content] if executable else substitutions(content, heredoc=True) if not quoted else []
                        )
                        break
                    body.append(line.lstrip("\t") if tabs else line)
                else:
                    raise ValueError("unfinished heredoc")
            pending = []
            line_start = i
    if pending:
        raise ValueError("unfinished heredoc")
    return "".join(kept), bodies


def join_continuations(text):
    kept, quote, i = [], None, 0
    while i < len(text):
        char = text[i]
        if char == "\\" and quote != "'":
            if text[i : i + 2] == "\\\n":
                i += 2
                continue
            kept.append(text[i : i + 2])
            i += 2
            continue
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        kept.append(char)
        i += 1
    return "".join(kept)
class Refused(Exception):
    pass
WARNED = []
def refused_root(path, cwd):
    path = resolve_path(path, cwd)
    home = os.path.expanduser('~')
    return (path in ('/', home, '/Users/aneyman')
            or re.fullmatch(r'/Volumes/[^/]+', path) is not None
            or re.fullmatch(r'/Volumes/StudioExt/repos(?:/[^/]+-wt)?', path) is not None
            or re.search(r'/\.agent-rails(?:/(?:agents|evidence|lanes))?$', path) is not None)
def wide_root(path, cwd):
    path = resolve_path(path, cwd)
    home = os.path.expanduser('~')
    return (path in ('/', home, '/Users/aneyman', '/System', '/Library', '/home', '/root',
                     '/Volumes/StudioExt/repos', home + '/repos')
            or re.fullmatch(r'/Volumes/[^/]+|/home/[^/]+(?:/repos)?', path) is not None
            or path.endswith('/.agent-rails') or path.endswith('/.agent-rails/lanes'))
def search_paths(name, args):
    paths, pattern, recursive = [], name == 'find' or (name == 'rg' and '--files' in args), name in ('rg', 'find', 'fd')
    word = None
    i, options = 0, True
    short_values = 'efmABCdD' if name in ('grep', 'egrep', 'fgrep', 'ggrep') else 'efgtdmABCj'
    long_values = {'--regexp', '--file', '--glob', '--iglob', '--type', '--type-not', '--max-depth',
                   '--maxdepth', '--max-count', '--threads', '--context', '--after-context',
                   '--before-context', '--include', '--exclude', '--exclude-dir', '--directories', '--devices'}
    while i < len(args):
        arg = args[i]
        i += 1
        if options and arg == '--':
            options = False
        elif options and arg.startswith('--'):
            option, eq, value = arg.partition('=')
            recursive |= option in ('--recursive', '--dereference-recursive')
            if option in long_values:
                if not eq and i < len(args):
                    value = args[i]
                    i += 1
                if option in ('--regexp', '--file') and word is None:
                    word = value
                pattern |= option in ('--regexp', '--file')
                recursive |= option == '--directories' and value == 'recurse'
        elif options and arg.startswith('-') and arg != '-':
            if name == 'find':
                if not paths and arg in ('-H', '-L', '-P', '-E', '-x', '-X', '-d', '-s'):
                    continue
                if arg == '-f' and i < len(args):
                    paths.append(args[i])
                    i += 1
                    continue
                break
            for j, flag in enumerate(arg[1:], 1):
                recursive |= name in ('grep', 'egrep', 'fgrep', 'ggrep') and flag in 'rR'
                pattern |= flag in 'ef'
                if flag in short_values:
                    value = arg[j + 1:]
                    if not value and i < len(args):
                        value = args[i]
                        i += 1
                    recursive |= flag == 'd' and value == 'recurse'
                    if flag in 'ef' and word is None:
                        word = value
                    break
        elif not pattern:
            pattern = True
            word = arg
        else:
            paths.append(arg)
    return paths, recursive, word
def shell_tokens(text):
    cleaned = []
    quote = None
    escaped = False
    boundary = True
    comment = False
    for char in text:
        if comment:
            if char != "\n":
                continue
            comment = False
        if escaped:
            escaped = False
            boundary = False
        elif char == "\\" and quote != "'":
            escaped = True
            boundary = False
        elif quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
            boundary = False
        elif char == "#" and boundary:
            comment = True
            continue
        else:
            boundary = char in " \t\r\n;&|()"
        cleaned.append(char)
    lexer = shlex.shlex("".join(cleaned), posix=True, punctuation_chars=";&|()<>\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def substitutions(text, heredoc=False):
    bodies = []
    quote = None
    boundary = True
    i = 0
    while i < len(text):
        char = text[i]
        if not heredoc and quote is None and char == "#" and boundary:
            end = text.find("\n", i)
            if end == -1:
                break
            i = end + 1
            continue
        if char == "\\" and quote != "'":
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
        elif char == "`" or text.startswith("$(", i):
            backtick = char == "`"
            start = i + (1 if backtick else 2)
            j, level, inner_quote = start, 1, None
            while j < len(text):
                c = text[j]
                if c == "\\" and inner_quote != "'":
                    j += 2
                    continue
                if backtick and c == "`":
                    break
                if inner_quote:
                    if c == inner_quote:
                        inner_quote = None
                elif c in "\"'":
                    inner_quote = c
                elif not backtick:
                    if c == "(":
                        level += 1
                    elif c == ")":
                        level -= 1
                        if level == 0:
                            break
                j += 1
            if j == len(text):
                raise ValueError("unfinished command substitution")
            bodies.append(text[start:j])
            i = j
        boundary = quote is None and char in " \t\r\n;&|()"
        i += 1
    return bodies
def resolve_path(path, cwd):
    path = re.sub(r'^\$(?:HOME|\{HOME\})(?=/|$)', os.path.expanduser('~'), path)
    if path.startswith('~'):
        home, _, rest = path.partition('/')
        path = (os.path.expanduser('~') if home == '~' else '/home/' + home[1:]) + '/' + rest
    return posixpath.normpath(path if path.startswith('/') else posixpath.join(cwd, path))
wrapper_values = {
    'env': ('uC', ('--unset', '--chdir', '--split-string')),
    'timeout': ('sk', ('--signal', '--kill-after')),
    'nice': ('n', ('--adjustment',)),
    'nohup': ('', ()),
    'command': ('', ()),
    'builtin': ('', ()),
    'time': ('', ()),
    'ionice': ('cnpPu', ('--class', '--classdata', '--pid', '--pgid', '--uid')),
    'arch': ('', ()),
    'setsid': ('', ()),
    'watch': ('nd', ('--interval', '--differences', '--shotsdir')),
    'sudo': ('ugphCUrRt', ('--user', '--group', '--prompt', '--host', '--chdir',
                        '--close-from', '--role', '--type', '--command-timeout')),
    'xargs': ('aEdILnPs', ('--arg-file', '--eof', '--delimiter', '--replace',
                         '--max-lines', '--max-args', '--max-procs', '--max-chars')),
    'stdbuf': ('ioe', ('--input', '--output', '--error')),
    'caffeinate': ('tw', ()),
    'gtimeout': ('sk', ('--signal', '--kill-after')),
    'ssh': ('BbcDEeFIiJLlmOopQRSWw', ()),
}
def after_options(args, i, name):
    short_values, long_values = wrapper_values[name]
    while i < len(args):
        arg = args[i]
        if arg == '--':
            return i + 1
        if not arg.startswith('-') or arg == '-':
            break
        i += 1
        if arg.startswith('--'):
            if '=' not in arg and arg in long_values:
                i += 1
        else:
            for j, flag in enumerate(arg[1:], 1):
                if flag in short_values:
                    if j == len(arg) - 1:
                        i += 1
                    break
    return i
def unsafe(args, depth, cwd):
    i = 0
    while i < len(args):
        if re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', args[i]):
            i += 1
            continue
        name = args[i].rsplit('/', 1)[-1]
        if name in ('do', 'then', 'else', 'elif', 'if', 'while', 'until', '{', '!', 'exec'):
            i += 1
            continue
        if name in ('bash', 'sh', 'zsh', 'dash', 'ksh'):
            j = i + 1
            while j < len(args):
                arg = args[j]
                if arg == '--' or not arg.startswith('-'):
                    break
                if not arg.startswith('--') and 'c' in arg[1:]:
                    command = j + 1
                    if command < len(args) and args[command] == '--':
                        command += 1
                    return command < len(args) and scan(args[command], depth + 1, cwd)
                j += 1
                if not arg.startswith('--') and any(flag in arg[1:] for flag in 'oO'):
                    j += 1
            return False
        if name == 'ssh':
            host = after_options(args, i + 1, name)
            remote = after_options(args, host + 1, name)
            return remote < len(args) and scan(' '.join(args[remote:]), depth + 1, cwd)
        if name not in wrapper_values:
            break
        if name == 'env':
            j = i + 1
            while j < len(args) and args[j].startswith('-'):
                arg = args[j]
                if arg in ('-S', '--split-string'):
                    return j + 1 < len(args) and scan(' '.join(args[j + 1:]), depth + 1, cwd)
                if arg.startswith('--split-string=') or arg.startswith('-S'):
                    value = arg.partition('=')[2] if arg.startswith('--') else arg[2:]
                    return scan(' '.join([value] + args[j + 1:]), depth + 1, cwd)
                if arg == '--':
                    break
                j += 1
                if arg in ('-u', '-C', '--unset', '--chdir'):
                    j += 1
        i = after_options(args, i + 1, name)
        if name == 'watch':
            return i < len(args) and scan(' '.join(args[i:]), depth + 1, cwd)
        if name in ('timeout', 'gtimeout'):
            i += 1
        elif name == 'nice' and i < len(args) and re.match(r'^\+?\d+$', args[i]):
            i += 1
    args = args[i:]
    if not args:
        return False
    name = args[0].rsplit('/', 1)[-1]
    tool = name
    if name == 'rtk':
        j = 1
        while j < len(args) and args[j].startswith('-'):
            j += 1
        sub = args[j] if j < len(args) else ''
        rest = args[j + 1:]
        if sub == 'run':
            for k, arg in enumerate(rest):
                if arg in ('-c', '--command') and k + 1 < len(rest):
                    return scan(rest[k + 1], depth + 1, cwd)
                if arg.startswith('--command='):
                    return scan(arg.partition('=')[2], depth + 1, cwd)
            return bool(rest) and scan(' '.join(rest), depth + 1, cwd)
        if sub in ('proxy', 'err', 'summary', 'test'):
            while rest and rest[0].startswith('-'):
                rest = rest[1:]
            return unsafe(rest, depth, cwd)
        if sub not in ('grep', 'rg', 'find'):
            return False
        tool, name, args = 'rtk ' + sub, ('find' if sub == 'find' else 'rg'), [sub] + rest
    if name == 'git' and 'grep' in args and '--no-index' in args:
        name, args = 'rg', ['rg'] + args[args.index('grep') + 1:]
        args = [arg for arg in args if arg != '--no-index']
    if name in ('rg', 'find', 'fd', 'grep', 'egrep', 'fgrep', 'ggrep'):
        roots, recursive, word = search_paths(name, args[1:])
        recursive |= tool.startswith('rtk ')
        content = name in ('rg', 'grep', 'egrep', 'fgrep', 'ggrep') and not any(arg in ('--files', '-l', '--files-with-matches', '--files-without-match') for arg in args)
        if name == 'find':
            content = any(arg in ('-exec', '-execdir', '-ok', '-okdir') for arg in args) or bool(re.search(r'\bxargs\b[^;]*\bcat\b', os.environ['SCAN_COMMAND']))
        if recursive:
            for path in (roots or ['.']):
                resolved = resolve_path(path, cwd)
                current = resolve_path('.', cwd)
                protected = resolved in (os.path.expanduser('~'), '/Users/aneyman') or (resolved != current and current.startswith(resolved.rstrip('/') + '/'))
                if content and protected:
                    raise Refused('BLOCKED (floor: secrets and protected credentials): recursive content reads must stay inside one repository or directory.')
                if refused_root(path, cwd) or wide_root(path, cwd):
                    WARNED.append(tool)
    if tool.startswith('rtk '):
        return False
    if name not in ('grep', 'egrep', 'fgrep', 'ggrep'):
        return False
    recursive = False
    skip = False
    pattern = False
    paths = []
    i = 1
    options = True
    while i < len(args):
        arg = args[i]
        if options and arg == '--':
            options = False
        elif options and arg.startswith('--'):
            name, _, value = arg.partition('=')
            long_names = ('--recursive', '--dereference-recursive', '--devices',
                          '--directories', '--regexp', '--file', '--max-count',
                          '--after-context', '--before-context', '--context',
                          '--include', '--exclude', '--exclude-from', '--exclude-dir',
                          '--binary-files', '--label', '--group-separator')
            matches = [option for option in long_names if option.startswith(name)]
            if len(matches) == 1:
                name = matches[0]
            recursive |= name in ('--recursive', '--dereference-recursive')
            if name in long_names[2:]:
                if not value and i + 1 < len(args):
                    i += 1
                    value = args[i]
                recursive |= name == '--directories' and value == 'recurse'
                if name == '--devices':
                    skip = value == 'skip'
                pattern |= name in ('--regexp', '--file')
        elif options and arg.startswith('-') and arg != '-':
            flags = arg[1:]
            j = 0
            while j < len(flags):
                flag = flags[j]
                recursive |= flag in 'rR'
                if flag in 'DdefmABC':
                    value = flags[j + 1:]
                    if not value and i + 1 < len(args):
                        i += 1
                        value = args[i]
                    recursive |= flag == 'd' and value == 'recurse'
                    if flag == 'D':
                        skip = value == 'skip'
                    pattern |= flag in 'ef'
                    break
                j += 1
        elif not pattern:
            pattern = True
        else:
            paths.append(arg)
        i += 1
    return recursive and not skip and any(
        '.agent-rails' in resolve_path(path, cwd).lower().split('/') for path in paths
    )
def scan(text, depth=0, cwd=None):
    if depth > 8:
        return True
    cwd = cwd or os.environ.get('SCAN_CWD') or os.getcwd()
    try:
        text = join_continuations(text)
        text, bodies = strip_heredocs(text)
        bodies.extend(substitutions(text))
        tokens = shell_tokens(text)
    except Exception as error:
        raise SystemExit(4)
    if any(scan(body, depth + 1, cwd) for body in bodies):
        return True
    segment = []
    piped = False
    items = iter(tokens + [';'])
    for token in items:
        if token and all(char in '<>&' for char in token) and ('<' in token or '>' in token):
            if segment and segment[-1].isdigit():
                segment.pop()
            target = next(items, None)
            if token == '<<<' and target is not None and any(
                word.rsplit('/', 1)[-1] in ('bash', 'sh', 'zsh', 'dash', 'ksh') for word in segment
            ) and scan(target, depth + 1, cwd):
                return True
            continue
        if token and all(char in ';&|()<>\n' for char in token):
            check_segment = segment
            if piped and segment and segment[0] in ("rg", "grep", "egrep", "fgrep"):
                roots, recursive, word = search_paths(segment[0], segment[1:])
                if not roots:
                    check_segment = []
            if unsafe(check_segment, depth, cwd):
                return True
            words = segment[:]
            while words and words[0] in ('do', 'then', 'else', 'elif', 'if', 'while', 'until', '{', '!'):
                words.pop(0)
            if words and words[0] in ('cd', 'pushd'):
                paths = [word for word in words[1:] if not word.startswith('-')]
                if paths:
                    cwd = resolve_path(paths[0], cwd)
                elif words[0] == 'cd':
                    cwd = os.path.expanduser('~')
            segment = []
            piped = token in ("|", "|&")
        else:
            segment.append(token)
    return False
try:
    fifo = scan(os.environ['SCAN_COMMAND'])
except Refused as refused:
    import sys
    print(refused.args[0], file=sys.stderr)
    raise SystemExit(2)
raise SystemExit(0 if fifo else 6 if WARNED else 3)
PY
if [ "$SCAN" -eq 0 ]; then
  warn "grep -r under ~/.agent-rails hangs on FIFOs; scope to one directory and use rg or add -D skip"
elif [ "$SCAN" -eq 2 ]; then
  exit 2
elif [ "$SCAN" -eq 6 ]; then
  case "$CMD" in *"# wide-scan-ok"*) allow ;; esac
  warn "recursive search over the whole disk, home, a volume root, all repos or ~/.agent-rails; scope it to one repo, lane or directory next time"
elif [ "$SCAN" -eq 4 ]; then
  case "$CMD" in *"# wide-scan-ok"*) allow ;; esac
  warn "could not parse this shell input, so it was not checked for a wide scan"
elif [ "$SCAN" -ne 3 ]; then
  refuse "scanner failed (python3 exit $SCAN)"
fi
allow
