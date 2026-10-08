#!/bin/bash
# Refuse recursive grep/rg/find over /, $HOME, /Volumes/<disk> or the whole repos dir.
# Such scans ran for minutes on 2026-09-25, pushed Studio load past 130 and tripped
# the OrbStack NFS dialog. Scope to one repo or directory instead.
# Override for a deliberate scan: append the comment "# wide-scan-ok".
INPUT=$(cat)
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty')
[ -z "$CMD" ] && exit 0
if SCAN_TEXT="$CMD" python3 - <<'DATA_PY'
import os, shlex
text = os.environ['SCAN_TEXT']
try:
    words = shlex.split(text)
    plain = words and words[0] in ('echo', 'printf') and not any(c in text for c in '|;&()<>`$\\\n\r')
except ValueError:
    plain = False
raise SystemExit(0 if plain else 1)
DATA_PY
then
  exit 0
fi
# Only CONTRACT_EOF files used as prompts are data, not arbitrary shell heredocs.
# See agent-lb config/coding-agents/agents/codex-verifier.md and its siblings.
CMD=$(printf '%s\n' "$CMD" | /usr/bin/awk '
  {
    lines[NR] = $0
    if (pending) {
      line = $0
      if (tabs[pending]) sub(/^\t+/, "", line)
      if (line == "CONTRACT_EOF") {
        last[pending] = NR
        pending = 0
      }
      next
    }
    writer = $0
    prefix = ""
    while (match(writer, /^[ \t]*(cd[ \t]+([^ \t;&|<>"\047`$]+|"[^"`$]+"|"\$[A-Za-z_][A-Za-z0-9_]*"|"\$\{[A-Za-z_][A-Za-z0-9_]*\}"|\047[^\047]+\047)|[A-Za-z_][A-Za-z0-9_]*=\$\(mktemp[ \t]+[^()`]*\))[ \t]*&&[ \t]*/)) {
      prefix = prefix substr(writer, 1, RLENGTH)
      writer = substr(writer, RLENGTH + 1)
    }
    if (writer ~ /^[ \t]*cat[ \t]+>[ \t]*("\$[A-Za-z_][A-Za-z0-9_]*"|\$[A-Za-z_][A-Za-z0-9_]*)[ \t]+<<-?\047CONTRACT_EOF\047[ \t]*$/) {
      pending = ++count
      first[count] = NR
      prefixes[count] = prefix
      tabs[count] = (writer ~ /<<-/)
      name = writer
      sub(/^[^$]*\$/, "", name)
      sub(/[^A-Za-z0-9_].*$/, "", name)
      names[count] = name
      checked = prefix
      gsub("(^|&&)[ \t]*" name "=\\$\\(mktemp[ \t]+[^()`]*\\)[ \t]*", "", checked)
      invalid[count] = (checked ~ /(^|&&)[ \t]*[A-Za-z_][A-Za-z0-9_]*=/)
    }
  }
  END {
    for (id = 1; id <= count; id++) {
      if (!last[id] || invalid[id]) continue
      text = prefixes[id] "\n"
      for (i = 1; i <= NR; i++)
        if (i < first[id] || i > last[id]) text = text lines[i] "\n"
      v = names[id]
      ref = "\\$(" v "([^A-Za-z0-9_]|$)|\\{" v "[^A-Za-z0-9_])"
      arg = "(\"\\$" v "\"|\\$" v "|\"\\$\\{" v "(:\\?)?\\}\"|\\$\\{" v "\\})"
      # Consume only complete arguments, not executable path suffixes or substitutions.
      prompt = "--prompt-file([ \t]+" arg "|=\"\\$" v "\"|=\"\\$\\{" v "(:\\?)?\\}\")([ \t\n;&|]|$)"
      while (match(text, "(^|[ \t])" prompt))
        text = substr(text, 1, RSTART - 1) " " substr(text, RSTART + RLENGTH - 1)
      cleanup = "(^|[;\n]|&&)[ \t]*rm[ \t]+-f([ \t]+" arg ")+[ \t]*([;\n]|&&|$)"
      while (match(text, cleanup))
        text = substr(text, 1, RSTART - 1) "\n" substr(text, RSTART + RLENGTH - 1)
      # Remove only the assignment name, leaving its contents checked for expansions.
      assignment = "(^|[;\n]|&&)[ \t]*" v "=\\$\\(mktemp[ \t]+"
      gsub(assignment, " mktemp ", text)
      drop[id] = (text !~ ref && text !~ ("(^|[^A-Za-z0-9_])" v "="))
    }
    for (i = 1; i <= NR; i++) {
      omit = 0
      for (id = 1; id <= count; id++)
        if (drop[id] && i > first[id] && i <= last[id]) omit = 1
      if (!omit) print lines[i]
    }
  }
')
# Check each grep separately: another command's -D skip cannot make it safe.
# Tokenizing keeps quoted paths intact and distinguishes patterns from paths.
if SCAN_COMMAND="$CMD" SCAN_CWD="$CWD" python3 - <<'PY'
import os
import shlex
import re
import posixpath


def shell_tokens(text):
    # shlex's commenters also eat mid-word '#'; shell comments start a word.
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


def substitutions(text):
    # Extract executable expansions even inside double quotes, never single quotes.
    bodies = []
    quote = None
    boundary = True
    i = 0
    while i < len(text):
        char = text[i]
        if quote is None and char == '#' and boundary:
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
        elif char == "'" and quote is None:
            quote = "'"
        elif char == '"':
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


def resolve_path(path, cwd):
    path = re.sub(r'^\$(?:HOME|\{HOME\})(?=/|$)', os.path.expanduser('~'), path)
    if path.startswith('~'):
        home, _, rest = path.partition('/')
        path = (os.path.expanduser('~') if home == '~' else '/home/' + home[1:]) + '/' + rest
    return posixpath.normpath(path if path.startswith('/') else posixpath.join(cwd, path))


# Options that consume the following word (attached values consume no word).
wrapper_values = {
    'env': ('uC', ('--unset', '--chdir', '--split-string')),
    'timeout': ('sk', ('--signal', '--kill-after')),
    'nice': ('n', ('--adjustment',)),
    'nohup': ('', ()),
    'command': ('', ()),
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
        if name in ('do', 'then', 'else', 'elif', 'if', 'while', 'until', '{', '!', 'time', 'exec'):
            i += 1
            continue
        if name in ('bash', 'sh', 'zsh', 'dash', 'ksh'):
            j = i + 1
            while j < len(args):
                arg = args[j]
                if arg == '--' or not arg.startswith('-'):
                    break
                if not arg.startswith('--') and 'c' in arg[1:]:
                    return j + 1 < len(args) and scan(args[j + 1], depth + 1, cwd)
                j += 1
                if not arg.startswith('--') and any(flag in arg[1:] for flag in 'oO'):
                    j += 1  # shell option name, e.g. -euo pipefail
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
        if name in ('timeout', 'gtimeout'):
            i += 1  # duration
        elif name == 'nice' and i < len(args) and re.match(r'^\+?\d+$', args[i]):
            i += 1
    args = args[i:]
    if not args or args[0].rsplit('/', 1)[-1] not in ('grep', 'egrep', 'fgrep', 'ggrep'):
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
        # Do not let additional remote-shell nesting bypass the FIFO policy.
        return True
    cwd = cwd or os.environ.get('SCAN_CWD') or os.getcwd()
    try:
        bodies = substitutions(text)
        tokens = shell_tokens(text)
    except ValueError:
        # Incomplete shell input remains subject to the existing width guard.
        return False
    if any(scan(body, depth + 1, cwd) for body in bodies):
        return True
    segment = []
    for token in tokens + [';']:
        if token and all(char in ';&|()<>\n' for char in token):
            if unsafe(segment, depth, cwd):
                return True
            # Track cd across a command list (including && and loop bodies).
            words = segment[:]
            while words and words[0] in ('do', 'then', 'else', 'elif', 'if', 'while', 'until', '{', '!'):
                words.pop(0)
            if words and words[0] == 'cd':
                paths = [word for word in words[1:] if not word.startswith('-')]
                if paths:
                    cwd = resolve_path(paths[0], cwd)
            segment = []
        else:
            segment.append(token)
    return False


raise SystemExit(0 if scan(os.environ['SCAN_COMMAND']) else 1)
PY
then
  REWRITE=$(HOOK_INPUT="$INPUT" python3 - <<'REWRITE_PY'
import json, os, re, shlex
try:
    data = json.loads(os.environ['HOOK_INPUT'])
    ti = data['tool_input']
    cmd = ti['command']
    match = re.match(r'^[ \t]*grep(?=[ \t])', cmd)
    # Deliberately conservative: no shell syntax, expansions, comments, escapes,
    # newlines, or later device flags that could override the inserted skip.
    if not match or any(c in cmd for c in '|;&()<>`$\\\n\r#'):
        raise ValueError('not a simple grep')
    words = shlex.split(cmd)
    if any(re.fullmatch(r'(?:/|~/?|/Users/aneyman/?|/Volumes/[^/\s]+/?|/Volumes/StudioExt/repos/?)', w)
           for w in words[1:]):
        raise ValueError('wide root')
    if any(w == '--devices' or w.startswith('--devices=') or
           (w.startswith('-') and not w.startswith('--') and 'D' in w[1:])
           for w in words[1:]):
        raise ValueError('device option')
    ti = dict(ti, command=cmd[:match.end()] + ' -D skip' + cmd[match.end():])
    print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse',
        'permissionDecision': 'allow', 'updatedInput': ti}}))
except (ValueError, TypeError, KeyError):
    pass
REWRITE_PY
)
  if [ -z "$REWRITE" ]; then
    echo 'grep -r under ~/.agent-rails hangs on FIFOs; use rg (skips FIFOs) or add -D skip' >&2
    exit 2
  fi
fi
case "$CMD" in *"# wide-scan-ok"*) [ -n "$REWRITE" ] && printf '%s\n' "$REWRITE"; exit 0 ;; esac
# Bulletin lookups: agents were running `rg -l --max-depth 4 <b-id> ~/.agent-rails` at 120% CPU each (Studio load 250, 2026-10-06).
LANES_ROOT='(^|[;&|(`[:space:]])(rg|grep|find|fd)[[:space:]][^;&|]*[[:space:]](~|\$HOME|"\$HOME"|/Users/aneyman)/\.agent-rails(/lanes)?/?([[:space:]]|$|;|\||\))'
if echo "$CMD" | grep -qE "$LANES_ROOT"; then
  echo "BLOCKED: do not rg/grep/find over ~/.agent-rails to find a bulletin or lane file; each scan ran at 120% CPU and stalled Studio. Use: lane-post read <b-id>  (prints the bulletin and its ref), lane-post list --to <pane> --limit N, or name the lane directory (~/.agent-rails/lanes/<lane>/). If it truly must be wide, add the comment '# wide-scan-ok'." >&2
  exit 2
fi
TOOL='(^|[;&|(`[:space:]])(grep[[:space:]]+([^;&|]*[[:space:]])?-[a-zA-Z]*[rR]|rg[[:space:]]|find[[:space:]]|fd[[:space:]])'
WIDE='[[:space:]](/|~|~/|\$HOME/?|"\$HOME"/?|/Users/aneyman/?|/Volumes/[^/[:space:]]+/?|/Volumes/StudioExt/repos/?|/System/?|/Library/?|/home/?|/home/[^/[:space:]]+/?|/home/[^/[:space:]]+/repos/?|/root/?)([[:space:]]|$|;|\||\))'
if echo "$CMD" | grep -qE "$TOOL" && echo "$CMD" | grep -qE "$WIDE"; then
  echo "BLOCKED: recursive search over the whole disk, home, a volume root or all repos. These scans stall Studio (load 130+) and trip the OrbStack NFS dialog. Scope it to one repo or directory, or use an index (git grep inside the repo). If it truly must be wide, add the comment '# wide-scan-ok'." >&2
  exit 2
fi
[ -n "$REWRITE" ] && printf '%s\n' "$REWRITE"
exit 0
