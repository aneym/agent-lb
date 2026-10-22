#!/bin/bash














set -euo pipefail
FLOOR="floor: destructive data with no approval on record"
approved() {
  python3 - "$CMD" <<'PY'
import os, re, sys
cmd = sys.argv[1]
quote = None
comment = None
escaped = False
for i, c in enumerate(cmd):
    if escaped:
        escaped = False
        continue
    if c == "\\" and quote != "'":
        escaped = True
    elif quote:
        if c == quote:
            quote = None
    elif c in "\"'":
        quote = c
    elif c == "#" and i and cmd[i-1].isspace():
        end = cmd.find("\n", i)
        end = len(cmd) if end < 0 else end
        if cmd[i:end].startswith("# alex-approval:") and not cmd[end:].strip():
            before = cmd[:i].rsplit("\n", 1)[-1]
            if before.strip():
                comment = cmd[i:end]
        break
m = re.fullmatch(r"#\s*alex-approval:\s*(\S+)\s*", comment or "")
if not m:
    sys.exit(1)
home = os.path.expanduser("~")
path = re.sub(r"^(?:~|\$HOME|\$\{HOME\})/", home + "/", m[1])
refs = home + "/.agent-rails/lanes/orchestrator-refs/"
name = path[len(refs):]
if (not path.startswith(refs) or ".." in path or "/" in name
        or not re.fullmatch(r"alex-.+-2026-.+\.md", name) or os.path.islink(path)):
    sys.exit(1)
try:
    with open(path) as f:
        sys.exit(0 if re.search(r"\b\d{1,2}:\d{2}\b", f.read()) else 1)
except OSError:
    sys.exit(1)
PY
}
refuse() {
  echo "BLOCKED: dangerous-command-guard $1, so this call is refused (the guard fails closed). Check jq and grep on PATH." >&2
  exit 2
}
allow() {
  if [ -n "${HOOK_FLOOR_RECEIPT:-}" ]; then printf 'floor-ok %s\n' dangerous-command-guard.sh; fi
  exit 0
}
INPUT=$(cat) || refuse "could not read the hook input (cat exit $?)"
CMD=$(jq -r '.tool_input.command // empty' <<<"$INPUT" 2>/dev/null) || refuse "could not read the hook input (jq exit $?)"
HOOK_CWD=$(jq -r '.cwd // empty' <<<"$INPUT" 2>/dev/null || true)
export HOOK_CWD
[ -n "$CMD" ] || allow

PROVIDER=$(python3 - "$CMD" <<'PY'
import os, re, shlex, sys
providers = {
    'muse': ('Muse Code', ('META_API_KEY',)),
    'railway': ('Railway', ('RAILWAY_TOKEN', 'RAILWAY_API_TOKEN')),
    'vercel': ('Vercel', ('VERCEL_TOKEN',)),
    'fly': ('Fly', ('FLY_API_TOKEN',)),
    'flyctl': ('Fly', ('FLY_API_TOKEN',)),
    'neonctl': ('Neon', ('NEON_API_KEY',)),
}
text = sys.argv[1]
RAILS = {'rails', 'rails-staging', 'worker-staging', 'postgres', 'postgres-staging'}
RAILS_FLOOR = ("kept floor: no acting in Alex's account; raw railway ssh, run or shell into the Rails services "
               "is refused. Use scripts/owner_admin.py or scripts/owner_admin_move.py with a test account")

def rails_target(words):
    if len(words) < 2 or words[1] not in ('ssh', 'run', 'shell'):
        return False
    for i, word in enumerate(words):
        if word.startswith('--service='):
            return word.split('=', 1)[1] in RAILS
        if word in ('-s', '--service'):
            return i + 1 < len(words) and words[i + 1] in RAILS
    return 'agent-rails' in os.environ.get('HOOK_CWD', '') + ' ' + text

match = re.search(r'\s+# alex-approval:\s*(\S+)\s*$', text)
if match and not re.search(r'\brailway\b', text):
    path = os.path.expanduser(match[1].replace('${HOME}', os.path.expanduser('~')).replace('$HOME', os.path.expanduser('~')))
    root = os.path.expanduser('~/.open-factory/approvals')
    if (os.path.dirname(path) == root and re.fullmatch(r'alex-.*\.md', os.path.basename(path))
            and not os.path.islink(path)):
        try:
            approval = open(path).read()
            exact = text[:match.start()].strip()
            if any(line.strip().strip(chr(96)) == exact for line in approval.splitlines()):
                sys.exit(0)
        except OSError:
            pass

def without_heredocs(source):

    lines = source.splitlines(keepends=True)
    clean = []
    delimiters = []
    for line in lines:
        if delimiters:
            if line.rstrip('\r\n').lstrip('\t') == delimiters[0]:
                delimiters.pop(0)
            continue
        clean.append(line)
        delimiters.extend(m[1] or m[2] or m[3] for m in re.finditer(
            r"<<-?\s*(?:'([^']+)'|\"([^\"]+)\"|([\w]+))", line))
    return ''.join(clean)

def substitutions(source):
    quote = None
    i = 0
    while i < len(source):
        char = source[i]
        if char == chr(92) and quote != chr(39):
            i += 2
            continue
        if char == chr(39) and quote != chr(34):
            quote = None if quote else char
        elif char == chr(34) and quote != chr(39):
            quote = None if quote else char
        elif quote != chr(39) and source.startswith('$(', i) and not source.startswith('$((', i):
            start = i + 2
            level = 1
            i = start
            while i < len(source) and level:
                if source[i] == '(': level += 1
                elif source[i] == ')': level -= 1
                i += 1
            if level: raise ValueError('unclosed substitution')
            yield source[start:i - 1]
            continue
        elif quote != chr(39) and char == chr(96):
            end = source.find(chr(96), i + 1)
            if end < 0: raise ValueError('unclosed substitution')
            yield source[i + 1:end]
            i = end
        i += 1

def segments(source):
    lexer = shlex.shlex(source, posix=True, punctuation_chars=';&|()<>\n')
    lexer.whitespace = ' \t\r'
    lexer.whitespace_split = True
    lexer.commenters = '#'
    words = []
    for word in lexer:
        if all(c in ';&|()<>\n' for c in word):
            if words:
                yield words
                words = []
        else:
            words.append(word)
    if words:
        yield words

def inspect(source, inherited, depth=0):
    if depth > 12:
        raise ValueError('shell nesting limit')
    source = without_heredocs(source)
    for body in substitutions(source):
        result = inspect(body, inherited, depth + 1)
        if result:
            return result
    source = re.sub(
        r'\$\(\s*agent-secret\s+get\s+railway-factory-prod-token\s*\)',
        'FACTORY_PROJECT_TOKEN', source)
    exported = dict(inherited)
    for words in segments(source):
        local = dict(exported)
        while words and (re.match(r'^[A-Za-z_][A-Za-z_0-9]*=', words[0]) or words[0] in ('env', 'command', 'exec')):
            word = words.pop(0)
            if word == 'env':
                while words and words[0].startswith('-'):
                    option = words.pop(0)
                    if option in ('-i', '--ignore-environment'):
                        local.clear()
                    elif option in ('-u', '--unset') and words:
                        local.pop(words.pop(0), None)
                    elif option.startswith('--unset='):
                        local.pop(option.split('=', 1)[1], None)
                    elif option == '--':
                        break
            elif word in ('command', 'exec'):
                while words and words[0].startswith('-'):
                    words.pop(0)
            if '=' in word:
                key, value = word.split('=', 1)

                ref = re.fullmatch(r'\$\{?([A-Za-z_][A-Za-z_0-9]*)\}?', value)
                local[key] = exported.get(ref[1], '') if ref else value
        if not words:
            continue
        name = os.path.basename(words[0])
        if name == 'export':
            for word in words[1:]:
                if '=' in word:
                    key, value = word.split('=', 1)
                    exported[key] = value
            continue
        if name == 'unset':
            for key in words[1:]:
                exported.pop(key, None)
            continue
        if name in ('bash', 'sh', 'zsh'):
            for i, word in enumerate(words[1:], 1):
                if word.startswith('-') and 'c' in word[1:] and i + 1 < len(words):
                    result = inspect(words[i + 1], local, depth + 1)
                    if result:
                        return result
                    break
        if name in ('npx', 'bunx'):
            args = words[1:]
            while args and args[0] in ('--yes', '-y', '--no-install', '--bun', '--'):
                args.pop(0)
            if args and re.fullmatch(r'(?:@railway/cli|railway)(?:@[^/]+)?', args[0]):
                words = ['railway', *args[1:]]
                name = 'railway'
        if name == 'railway' and (words[1:2] in (['--help'], ['-h']) or words[2:3] in (['--help'], ['-h']) or words[1:] in (['--version'], ['-V']) or words[1:2] == ['help']):
            continue
        if name == 'railway' and words[1:2] == ['login']:
            return "kept floor: Alex's own Railway login. Use a factory-provisioned RAILWAY_TOKEN"
        if name == 'railway' and rails_target(words):
            return RAILS_FLOOR
        if name in providers:
            label, keys = providers[name]
            if not any(local.get(key) for key in keys):
                return f"kept floor: Alex{chr(39)}s own {label} login. Use a factory-provisioned {keys[0]} (ask ub_vaxptt)"
    return ''
try:
    print(inspect(text, os.environ))
except ValueError:
    print("WARNING: provider guard could not parse executable argv; allowing command.", file=sys.stderr)
PY
) || refuse "could not inspect provider argv"
if [ -n "$PROVIDER" ]; then
  printf 'BLOCKED: %s\n' "$PROVIDER" >&2
  exit 2
fi
SQL=$(python3 - "$CMD" <<'PY'
import os, re, shlex, sys

def commands(text):
    lines = text.splitlines(keepends=True)
    clean = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.search(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z_0-9]*)\1", line)
        clean.append(line)
        i += 1
        if m:
            body = []
            while i < len(lines) and lines[i].strip() != m[2]:
                body.append(lines[i]); i += 1
            i += 1
            if re.match(r"\s*(?:bash|sh|zsh)\b", line):
                yield from commands(''.join(body))
    lexer = shlex.shlex(''.join(clean), posix=True, punctuation_chars=';|&<>')
    lexer.whitespace = ' \t\r'
    lexer.commenters = '#'
    segment = []
    for token in lexer:
        if token == '\n' or token in (';', '&&', '||', '|', '&'):
            if segment:
                yield segment
                segment = []
        else:
            segment.append(token)
    if segment:
        yield segment

def read_file(path):
    try:
        if os.path.getsize(path) >= 1_000_000:
            raise OSError('larger than limit')
        with open(path) as f:
            return f.read(1_000_000)
    except (OSError, UnicodeError):
        print('dangerous-command-guard: SQL input unreadable or >=1 MB; passing without inspection.', file=sys.stderr)
        return ''

parts = list(commands(sys.argv[1]))
for index, words in enumerate(parts):
    name = os.path.basename(words[0])
    if name in ('bash', 'sh', 'zsh') and '-c' in words:

        at = words.index('-c')
        if at + 1 < len(words):
            parts.extend(commands(words[at + 1]))
    if name != 'psql':
        continue
    for i, word in enumerate(words[1:], 1):
        if word in ('-c', '--command') and i + 1 < len(words):
            print(words[i + 1])
        elif word.startswith('--command='):
            print(word.split('=', 1)[1])
        elif word in ('-f', '--file', '<') and i + 1 < len(words):
            print(read_file(words[i + 1]))
        elif word.startswith('--file='):
            print(read_file(word.split('=', 1)[1]))
    if index and parts[index-1][0] == 'cat' and len(parts[index-1]) == 2:
        print(read_file(parts[index-1][1]))

PY
) || {
  if grep -qiE 'DROP|TRUNCATE|launchctl[[:space:]]+disable' <<<"$CMD"; then
    refuse "could not parse a kept-floor command"
  fi
  echo "WARNING: destructive-data guard could not parse executable argv; allowing command." >&2
  allow
}
MATCH=0
grep -qiE '(DROP\s+(DATABASE|TABLE)|TRUNCATE\s+(TABLE\s+)?[A-Za-z_])' <<<"$SQL" || MATCH=$?
if [ "$MATCH" -eq 0 ] && ! approved; then
  echo "BLOCKED: Dangerous command requires explicit user approval ($FLOOR). Approved: append ' # alex-approval: ~/.agent-rails/lanes/orchestrator-refs/alex-<topic>-2026-<date>.md' (a first-hand Alex quote with a time)." >&2
  exit 2
fi
[ "$MATCH" -le 1 ] || refuse "could not match the command (grep exit $MATCH: missing or failed)"
allow
