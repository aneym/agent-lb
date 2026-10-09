#!/bin/bash
# PreToolUse guard: agent-lb restarts go through lb-restart (blue/green, 2026-09-25).
# A raw kickstart/bootout of the service or the front holds every new connection
# while the old process drains (10-95 s measured 2026-09-25) and can cut streams.
# Escape hatch when lb-restart itself is broken: touch ~/.agent-lb/raw-restart.ok
# Adopted into agent-lb 2026-10-08 (hook-dispatcher-4): edit this source only. It is a floor guard, so input it
# cannot read (jq missing or failing) is refused, never allowed as an empty command. jq reads the input itself (a
# missing `cat` before it read as an empty command), and a matcher that fails (grep missing or erroring, exit > 1) is
# told apart from no match (exit 1) and refuses (2026-10-08 review: a missing grep let a raw kickstart through).
# S44 (2026-10-08): reads are checked (set -euo pipefail) and jq must read exactly one JSON input (`input` fails on an
# empty one); an allow ends with the receipt line `floor-ok agent-lb-bootout-guard.sh` when the dispatcher asks for it
# (HOOK_FLOOR_RECEIPT), and the dispatcher refuses an exit 0 without it.
# Guard trim (Alex, 2026-10-08 09:18 ET): a kept floor (keeps the orchestrators up). Its message names the floor and
# it accepts the Rails CoS approval record: a trailing `# alex-approval: <path>` naming an existing
# ~/.agent-rails/lanes/orchestrator-refs/alex-*-2026-*.md that carries a time.
set -euo pipefail
FLOOR="floor: keeps the orchestrators up (agent-lb carries every agent session)"
APPROVAL="Approved restarts pass with a trailing '# alex-approval: ~/.agent-rails/lanes/orchestrator-refs/alex-<topic>-2026-<date>.md' (a first-hand Alex quote with a time)."
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
allow() {
  if [ -n "${HOOK_FLOOR_RECEIPT:-}" ]; then printf 'floor-ok %s\n' agent-lb-bootout-guard.sh; fi
  exit 0
}
# Exit 0: a command that runs restarts agent-lb; 1: none does; anything else: the parser failed.
runs() {
  python3 - "$CMD" <<'PY'
import os, re, shlex, sys

LABEL = re.compile(r"com\.aneyman\.agent-lb(-front)?(?![A-Za-z0-9_-])")
VERBS = {"bootout", "kickstart", "stop", "kill", "unload", "remove"}
WRAPPERS = {"sudo", "env", "command", "exec", "nohup", "time", "nice", "caffeinate", "stdbuf", "xargs", "timeout"}
SHELLS = {"bash", "sh", "zsh", "dash"}
KEYWORDS = {"if", "then", "do", "while", "until", "else", "elif", "!", "{", "}", "fi", "done"}
OPERATORS = "<>;&|()"
HEREDOC = re.compile(r"(?<!<)<<(?!<)-?\s*(\\?)(['\"]?)([A-Za-z0-9_.-]+)\2")


class Doubt(Exception):
    pass


def unquoted(line, quote):
    free, k = set(), 0
    while k < len(line):
        c = line[k]
        if quote == "'":
            quote = None if c == "'" else quote
        elif quote == '"':
            if c == "\\":
                k += 1
            elif c == '"':
                quote = None
        elif c == "\\":
            k += 1
        elif c in "'\"":
            quote = c
        elif c == "#" and (k == 0 or line[k - 1] in " \t\n;&|()"):
            k = line.find("\n", k)
            if k < 0:
                break
            continue
        else:
            free.add(k)
        k += 1
    return free, quote


def first_word(prefix):
    part = re.split(r"[;&|(]", prefix)[-1]
    try:
        words = shlex.split(part)
    except ValueError:
        words = part.split()
    words = [w for w in words if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w)]
    while words and (os.path.basename(words[0]) in WRAPPERS or words[0].startswith("-")):
        words = words[1:]
    return os.path.basename(words[0]) if words else ""


def split_heredocs(text):
    """Text without heredoc bodies; unquoted bodies (their $( ) runs); bodies fed to a shell (they run)."""
    text = text.replace("\\\n", "")
    out, live, shell, ends, quote = [], [], [], [], None
    for line in text.split("\n"):
        if ends:
            end, is_live, to_shell, body = ends[0]
            if line.strip() == end or line.lstrip("\t") == end:
                ends.pop(0)
                if to_shell:
                    shell.append("\n".join(body))
            else:
                body.append(line)
                if is_live:
                    live.append(line)
            continue
        out.append(line)
        free, next_quote = unquoted(line, quote)
        if quote is None:
            ends += [(m.group(3), not (m.group(1) or m.group(2)), first_word(line[:m.start()]) in SHELLS, [])
                     for m in HEREDOC.finditer(line) if m.start() in free]
        quote = next_quote
    if ends:
        raise Doubt
    return "\n".join(out), "\n".join(live), shell


def substitutions(text):
    """$( ) and backtick bodies outside single quotes: they run, even inside double quotes."""
    bodies, quote, k = [], None, 0
    while k < len(text):
        c = text[k]
        if quote == "'":
            quote = None if c == "'" else quote
        elif c == "\\":
            k += 1
        elif c == "'" and quote is None:
            quote = "'"
        elif c == '"':
            quote = None if quote == '"' else '"'
        elif c == "`":
            end = text.find("`", k + 1)
            if end < 0:
                raise Doubt
            bodies.append(text[k + 1:end])
            k = end
        elif text.startswith("$(", k):
            depth, j = 1, k + 2
            while j < len(text) and depth:
                depth += {"(": 1, ")": -1}.get(text[j], 0)
                j += 1
            if depth:
                raise Doubt
            bodies.append(text[k + 2:j - 1])
            k = j - 1
        k += 1
    return bodies


def simple_commands(text):
    if any(0xE000 <= ord(c) < 0xE000 + len(OPERATORS) for c in text):
        raise Doubt
    free, _ = unquoted(text, None)
    text = "".join(("\n ; " if c == "\n" else c) if k in free else
                   chr(0xE000 + OPERATORS.index(c)) if c in OPERATORS else c for k, c in enumerate(text))
    restore = {0xE000 + i: c for i, c in enumerate(OPERATORS)}
    lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        words = list(lexer)
    except ValueError:
        raise Doubt
    command, target = [], False
    for word in words + [";"]:
        if set(word) <= set(";&|()<>") and ("<" in word or ">" in word) and "(" not in word:
            if command and command[-1].isdigit():
                command.pop()
            target = True
        elif set(word) <= set(";&|()<>"):
            if command:
                yield command
            command, target = [], False
        elif target:
            target = False
        else:
            command.append(word.translate(restore))


def shell_string(args):
    for i, arg in enumerate(args):
        if arg == "--" or not arg.startswith(("-", "+")):
            return None
        if arg.startswith("-") and not arg.startswith("--") and "c" in arg[1:]:
            rest = [a for a in args[i + 1:] if not a.startswith(("-", "+"))]
            return rest[0] if rest else None
    return None


def restarts(text, depth=0):
    if depth > 4:
        raise Doubt
    text, live, shell = split_heredocs(text)
    for body in substitutions(text) + substitutions(live) + shell:
        if restarts(body, depth + 1):
            return True
    for argv in simple_commands(text):
        while argv and argv[0] in KEYWORDS:
            argv = argv[1:]
        while argv and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", argv[0]) or os.path.basename(argv[0]) in WRAPPERS):
            # Skip a wrapper and its options; the command it runs is the next word naming a command we judge.
            later = [i for i, a in enumerate(argv) if i and os.path.basename(a) in SHELLS | {"launchctl", "eval"}]
            argv = argv[later[0]:] if later else []
        if not argv:
            continue
        name = os.path.basename(argv[0])
        if name == "launchctl" and VERBS & set(argv[1:]) and any(LABEL.search(a) for a in argv[1:]):
            return True
        if name in SHELLS:
            script = shell_string(argv[1:])
            if script is not None and restarts(script, depth + 1):
                return True
        if name == "eval" and restarts(" ".join(argv[1:]), depth + 1):
            return True
    return False


try:
    sys.exit(0 if restarts(sys.argv[1]) else 1)
except Doubt:
    sys.exit(0)
PY
}
JQ=/opt/homebrew/bin/jq
[ -x "$JQ" ] || JQ=jq
if ! CMD=$("$JQ" -n -r 'input | .tool_input.command // empty' 2>/dev/null); then
  echo "BLOCKED ($FLOOR): agent-lb-bootout-guard could not read the hook input (jq missing or failed), so the call is refused." >&2
  exit 2
fi
[ -n "$CMD" ] || allow
case "$CMD" in *launchctl*) ;; *) allow ;; esac  # the pattern needs `launchctl`; the dispatcher's prefilter is this
MATCH=0
grep -qE 'launchctl[^|;&]*(bootout|kickstart|stop|kill)[^|;&]*com\.aneyman\.agent-lb(-front)?([^a-zA-Z0-9_-]|$)' <<<"$CMD" || MATCH=$?
if [ "$MATCH" -gt 1 ]; then
  echo "BLOCKED ($FLOOR): agent-lb-bootout-guard could not match the command (grep exit $MATCH: missing or failed), so the call is refused." >&2
  exit 2
fi
if [ "$MATCH" -eq 0 ]; then
  # The grep is a prefilter (guard rule 2026-10-08, factory-operations 165): a hit counts only when a command that runs
  # here is launchctl with a restart verb and the agent-lb label. Heredoc bodies written to a file, quoted arguments,
  # echo and commit text, and ssh arguments (another host) are data. A body fed to a shell, `bash -c`, `eval`, $( )
  # and backticks run and are read. Text the parser cannot read counts as a hit (fail closed).
  RUNS=0
  runs || RUNS=$?
  if [ "$RUNS" -gt 1 ]; then
    echo "BLOCKED ($FLOOR): agent-lb-bootout-guard could not parse the command (python3 exit $RUNS), so the call is refused." >&2
    exit 2
  fi
  [ "$RUNS" -eq 0 ] || allow
  if [ ! -f "$HOME/.agent-lb/raw-restart.ok" ] && ! approved; then
    {
      echo "BLOCKED ($FLOOR): raw launchctl restart of agent-lb. Use the blue/green restart instead:"
      echo "  ~/.agent-lb/bin/lb-restart --reason \"<what>\" [--from <worktree> --files <paths>]"
      echo "  ~/.agent-lb/bin/lb-restart --reason \"<what>\" --check   # boot+health-check only"
      echo "Add --reload-plist after editing the launchd plist."
      echo "Only if lb-restart itself is broken: touch ~/.agent-lb/raw-restart.ok, then remove it."
      echo "$APPROVAL"
    } >&2
    exit 2
  fi
fi
allow
