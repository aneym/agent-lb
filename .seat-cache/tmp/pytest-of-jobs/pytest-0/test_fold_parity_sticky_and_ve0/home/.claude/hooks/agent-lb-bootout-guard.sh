#!/bin/bash














set -euo pipefail
FLOOR="floor: keeps the orchestrators up (agent-lb carries every agent session)"
allow() {
  if [ -n "${HOOK_FLOOR_RECEIPT:-}" ]; then printf 'floor-ok %s\n' agent-lb-bootout-guard.sh; fi
  exit 0
}

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
INPUT=
READ=0
IFS= read -r -d '' INPUT || READ=$?
if [ "$READ" -gt 1 ]; then
  echo "BLOCKED ($FLOOR): agent-lb-bootout-guard could not read the hook input (read exit $READ), so the call is refused." >&2
  exit 2
fi
if ! CMD=$(printf '%s' "$INPUT" | "$JQ" -r '.tool_input.command // empty' 2>/dev/null); then
  echo "BLOCKED ($FLOOR): agent-lb-bootout-guard could not read the hook input (jq missing or failed), so the call is refused." >&2
  exit 2
fi
[ -n "$CMD" ] || allow
case "$CMD" in *launchctl*) ;; *) allow ;; esac
MATCH=0
grep -qE 'launchctl[^|;&]*(bootout|kickstart|stop|kill)[^|;&]*com\.aneyman\.agent-lb(-front)?([^a-zA-Z0-9_-]|$)' <<<"$CMD" || MATCH=$?
if [ "$MATCH" -gt 1 ]; then
  echo "BLOCKED ($FLOOR): agent-lb-bootout-guard could not match the command (grep exit $MATCH: missing or failed), so the call is refused." >&2
  exit 2
fi
if [ "$MATCH" -eq 0 ]; then




  RUNS=0
  runs || RUNS=$?
  if [ "$RUNS" -gt 1 ]; then
    echo "BLOCKED ($FLOOR): agent-lb-bootout-guard could not parse the command (python3 exit $RUNS), so the call is refused." >&2
    exit 2
  fi
  [ "$RUNS" -eq 0 ] || allow
  HOOK_INPUT="$INPUT" python3 - <<'REWRITE_PY'
import json, os, shlex, sys
path = os.path.expanduser("~/.agent-lb/bin/lb-restart")
if not os.path.isfile(path) or not os.access(path, os.X_OK):
    print("BLOCKED (floor: shared live sessions): blue/green restart is unavailable; raw launchctl restart was not run.", file=sys.stderr)
    sys.exit(2)
data = json.loads(os.environ["HOOK_INPUT"])
tool = data.get("tool_input") or {}
command = tool.get("command", "")
try:
    words = shlex.split(command)
except ValueError:
    words = []
if (not words or os.path.basename(words[0]) != "launchctl"
        or any(c in command for c in ";&|()<>`$\\\n\r")
        or len(words) < 3 or words[1] not in {"kickstart", "bootout", "stop", "kill", "unload", "remove"}):
    print("BLOCKED (floor: shared live sessions): cannot safely rewrite this compound restart; use ~/.agent-lb/bin/lb-restart --reason <reason> explicitly.", file=sys.stderr)
    sys.exit(2)
updated = dict(tool, command=shlex.quote(path) + " --reason " + shlex.quote("guard: replace raw launchctl restart with blue/green restart"))
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": updated,
    "additionalContext": "agent-lb-bootout-guard: replacing raw launchctl restart with lb-restart blue/green; unrelated commands are never discarded."}}))
REWRITE_PY
fi
allow
