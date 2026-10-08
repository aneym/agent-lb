#!/bin/bash
# PreToolUse Bash floor guard: `rm -rf /...` and `DROP DATABASE|TABLE` need explicit user approval.
# Until 2026-10-08 this was an inline leaf in settings.json:
#   bash -c 'CMD=$(cat | jq -r ".tool_input.command // empty"); [ -z "$CMD" ] && exit 0; echo "$CMD" | grep -qiE ...'
# Review M3 (hook-dispatcher-5): with jq or grep failing, that shell turned the failure into exit 0 and allowed
# `psql -c "DROP DATABASE app"`. S44 moved it here: install-policy.py installs this file and registers it in place of
# the inline leaf, and the dispatcher runs it as a direct exec. Every read and the match are checked: jq must read
# exactly one JSON input, grep must answer match (0) or no match (1); anything else refuses. An allow ends with the
# receipt line `floor-ok dangerous-command-guard.sh` when the dispatcher asks for it (HOOK_FLOOR_RECEIPT); the
# dispatcher refuses an exit 0 without it. The match is the inline leaf's, unchanged (hook-dispatch.py pf_dangerous
# is its prefilter).
# Guard trim (Alex, 2026-10-08 09:18 ET: "dont have guards that are too aggressive"): the recursive root delete
# moved to rm-dynamic-deny, which reads the command that runs (this grep matched heredoc and ssh text). This guard
# keeps the destructive-data floor, names it, and accepts the Rails CoS approval record: a trailing
# `# alex-approval: <path>` naming an existing ~/.agent-rails/lanes/orchestrator-refs/alex-*-2026-*.md with a time.
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
CMD=$(jq -n -r 'input | .tool_input.command // empty' 2>/dev/null) || refuse "could not read the hook input (jq exit $?)"
[ -n "$CMD" ] || allow
SQL=$(python3 - "$CMD" <<'PY'
import os, re, shlex, sys
# Quoted heredocs sent to data readers are inert. Shell consumers are reparsed.
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
        # Shell strings execute, unlike an ssh argument or echo's quoted words.
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
# DROP/**/TABLE is an accepted residual: this accidental-command guard does not normalize SQL comments.
PY
) || refuse "could not parse the shell command"
MATCH=0
grep -qiE '(DROP\s+(DATABASE|TABLE)|TRUNCATE\s+(TABLE\s+)?[A-Za-z_])' <<<"$SQL" || MATCH=$?
if [ "$MATCH" -eq 0 ] && ! approved; then
  echo "BLOCKED: Dangerous command requires explicit user approval ($FLOOR). Approved: append ' # alex-approval: ~/.agent-rails/lanes/orchestrator-refs/alex-<topic>-2026-<date>.md' (a first-hand Alex quote with a time)." >&2
  exit 2
fi
[ "$MATCH" -le 1 ] || refuse "could not match the command (grep exit $MATCH: missing or failed)"
allow
