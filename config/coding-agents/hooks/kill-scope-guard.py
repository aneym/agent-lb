#!/usr/bin/env python3
"""PreToolUse(Bash) guard: a pattern kill must not reach other agents' processes.

Why (factory-operations 60): on 2026-10-07 23:55 ET comms-lead ran `pgrep -f 'no:cacheprovider' | xargs kill` and
SIGTERMed three other agents' pytest runs. Every agent on this machine shares one user, so a pattern kill reaches
every session's processes.

What it judges: the commands that run (heredoc bodies written to files, quoted text, echo and commit messages, and
ssh arguments are data): `pkill`/`killall`, `pgrep ... | xargs kill`, and `kill $(pgrep ...)` or backticks. It
evaluates the pattern against the live process table (`ps -axo pid=,ppid=,command=`):
- every match is this session's own process (the agent's Claude process and its descendants) or carries this
  session's id in its command line: allow;
- a match is another session's process: deny, naming the floor;
- the pattern cannot be evaluated (built at run time, unreadable options): warn and allow.
`pkill -P $$` and other parent-scoped kills of the session's own tree are allowed.

Fixed contract: the Claude Code hook JSON on stdin; exit 0 to allow (a warning is a one-line hookSpecificOutput
additionalContext), exit 2 with a BLOCKED line on stderr to deny. Test seam: KILL_GUARD_PS names a JSON file of
[{pid, ppid, command}] used instead of ps, and KILL_GUARD_SELF the pid the session walk starts from.
"""
import json
import os
import re
import shlex
import subprocess
import sys

FLOOR = "floor: other agents' running work (a kill must stay inside this session)"
WRAPPERS = {"sudo", "env", "command", "exec", "nohup", "time", "nice", "caffeinate", "stdbuf", "timeout"}
SHELLS = {"bash", "sh", "zsh", "dash"}
KEYWORDS = {"if", "then", "do", "while", "until", "else", "elif", "!", "{", "}", "fi", "done"}
OPERATORS = "<>;&|()"
HEREDOC = re.compile(r"(?<!<)<<(?!<)-?\s*(\\?)(['\"]?)([A-Za-z0-9_.-]+)\2")
# pgrep/pkill options that take a value (macOS and procps).
VALUED = set("FGgPstUu")


class Doubt(Exception):
    """The command cannot be read."""


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
    """(body, kill_before) for each $( ) and backtick body outside single quotes."""
    found, quote, k = [], None, 0
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
        elif c == "`" or text.startswith("$(", k):
            if c == "`":
                end = text.find("`", k + 1)
                if end < 0:
                    raise Doubt
                body, nxt = text[k + 1:end], end
            else:
                depth, j = 1, k + 2
                while j < len(text) and depth:
                    depth += {"(": 1, ")": -1}.get(text[j], 0)
                    j += 1
                if depth:
                    raise Doubt
                body, nxt = text[k + 2:j - 1], j - 1
            head = re.split(r"[;&|\n]", text[:k])[-1]
            found.append((body, re.search(r"(^|\s)(\S*/)?kill(\s|$)", head) is not None))
            k = nxt
        k += 1
    return found


def pipelines(text):
    """Each pipeline as a list of argv lists."""
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
    pipe, command, target = [], [], False
    for word in words + [";"]:
        if set(word) <= set(";&|()<>") and ("<" in word or ">" in word) and "(" not in word:
            if command and command[-1].isdigit():
                command.pop()
            target = True
        elif set(word) <= set(";&|()<>"):
            if command:
                pipe.append(command)
            command, target = [], False
            if word not in ("|", "|&") and pipe:
                yield pipe
                pipe = []
        elif target:
            target = False
        else:
            command.append(word.translate(restore))


def unwrap(argv):
    while argv and argv[0] in KEYWORDS:
        argv = argv[1:]
    while argv and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", argv[0]) or os.path.basename(argv[0]) in WRAPPERS):
        argv = argv[1:]
        while argv and (argv[0].startswith("-") or re.fullmatch(r"[0-9.]+[smhd]?", argv[0])):
            argv = argv[1:]
    return argv


def shell_string(args):
    for i, arg in enumerate(args):
        if arg == "--" or not arg.startswith(("-", "+")):
            return None
        if arg.startswith("-") and not arg.startswith("--") and "c" in arg[1:]:
            rest = [a for a in args[i + 1:] if not a.startswith(("-", "+"))]
            return rest[0] if rest else None
    return None


def selector(argv):
    """A pgrep/pkill/killall argv as a selection: {full, exact, ignore, parents, pattern} or None if unreadable."""
    name = os.path.basename(argv[0])
    sel = {"full": False, "exact": name == "killall", "ignore": False, "parents": None, "pattern": None,
           "tool": name}
    args, i = argv[1:], 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            rest = args[i + 1:]
            sel["pattern"] = rest[-1] if rest else None
            break
        if arg.startswith("-") and len(arg) > 1 and not re.fullmatch(r"-\d+|-(SIG)?[A-Z]{2,}[0-9]*", arg):  # a signal is not an option
            flags = arg[1:]
            for j, flag in enumerate(flags):
                if flag in VALUED and name != "killall":
                    value = flags[j + 1:] or (args[i + 1] if i + 1 < len(args) else None)
                    if not flags[j + 1:]:
                        i += 1
                    if flag == "P":
                        sel["parents"] = value
                    break
                sel["full"] |= flag == "f"
                sel["exact"] |= flag == "x"
                sel["ignore"] |= flag == "i"
        elif not arg.startswith("-"):
            sel["pattern"] = arg
        i += 1
    return sel


def kills(text, depth=0):
    """Every pattern kill that runs in this text, as selections."""
    if depth > 4:
        raise Doubt
    text, live, shell = split_heredocs(text)
    out = []
    for body, after_kill in substitutions(text) + substitutions(live):
        for pipe in pipelines(body):
            first = unwrap(pipe[0])
            if after_kill and first and os.path.basename(first[0]) == "pgrep":
                out.append(selector(first))
        out += kills(body, depth + 1)
    for body in shell:
        out += kills(body, depth + 1)
    for pipe in pipelines(text):
        argvs = [unwrap(argv) for argv in pipe]
        for n, argv in enumerate(argvs):
            if not argv:
                continue
            name = os.path.basename(argv[0])
            if name in ("pkill", "killall"):
                out.append(selector(argv))
            elif name == "pgrep" and n + 1 < len(argvs):
                nxt = argvs[n + 1]
                if nxt and os.path.basename(nxt[0]) == "xargs":
                    rest = nxt[1:]
                    while rest and rest[0].startswith("-"):
                        rest = rest[1:]
                    if rest and os.path.basename(rest[0]) == "kill":
                        out.append(selector(argv))
            elif name in SHELLS:
                script = shell_string(argv[1:])
                if script is not None:
                    out += kills(script, depth + 1)
            elif name == "eval":
                out += kills(" ".join(argv[1:]), depth + 1)
    return out


def process_table():
    fake = os.environ.get("KILL_GUARD_PS")
    if fake:
        with open(fake) as handle:
            return {int(row["pid"]): (int(row["ppid"]), row["command"]) for row in json.load(handle)}
    out = subprocess.run(["/bin/ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True, timeout=10)
    table = {}
    for line in out.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[2] if len(parts) > 2 else "")
    return table


def session_root(table, start):
    """The agent's Claude process above this hook, or the hook's parent when none is found."""
    pid, seen = start, set()
    while pid in table and pid not in seen and pid > 1:
        seen.add(pid)
        command = table[pid][1]
        exe = command.split()[0] if command.split() else ""
        if os.path.basename(exe) == "claude" or re.search(r"(^|/)claude(\s|$)|@anthropic-ai/claude-code", command):
            return pid
        pid = table[pid][0]
    return table.get(start, (start, ""))[0] if start in table else start


def descendants(table, root):
    own, frontier = {root}, [root]
    children = {}
    for pid, (ppid, _) in table.items():
        children.setdefault(ppid, []).append(pid)
    while frontier:
        for child in children.get(frontier.pop(), []):
            if child not in own:
                own.add(child)
                frontier.append(child)
    return own


def matches(sel, table):
    pattern = sel["pattern"]
    flags = re.IGNORECASE if sel["ignore"] else 0
    rx = re.compile(pattern if not sel["tool"] == "killall" else re.escape(pattern), flags)
    hit = []
    for pid, (ppid, command) in table.items():
        if pid == os.getpid():
            continue
        words = command.split()
        subject = command if sel["full"] else os.path.basename(words[0]) if words else ""
        found = rx.fullmatch(subject) if sel["exact"] else rx.search(subject)
        if found:
            hit.append(pid)
    return hit


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0  # not a hook payload this guard can read; the kill-guard wrapper still checks pkill at run time
    command = (payload.get("tool_input") or {}).get("command") or ""
    if not re.search(r"\b(pkill|killall|pgrep)\b", command):
        return 0
    try:
        selections = kills(command)
    except Doubt:
        selections = None
    if selections == []:
        return 0
    if selections is None or any(s["pattern"] is None and s["parents"] is None for s in selections) or any(
            s["pattern"] and re.search(r"[$`]", s["pattern"]) for s in selections):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": (
            "kill-scope-guard (warn only, not blocked): this kill's pattern cannot be read before it runs; make sure "
            "it matches only this session's processes (kill <pid>, or pkill -P $$).")}}))
        return 0
    table = process_table()
    start = int(os.environ.get("KILL_GUARD_SELF") or os.getppid())
    own = descendants(table, session_root(table, start))
    session = str(payload.get("session_id") or "")
    foreign = []
    for sel in selections:
        if sel["parents"] is not None:
            parents = [p for p in re.split(r"[ ,]", sel["parents"]) if p]
            if all(p in ("$$", "$PPID") or (p.isdigit() and int(p) in own) for p in parents):
                continue
            foreign.append((sel, ["parent " + ",".join(parents)]))
            continue
        try:
            hit = matches(sel, table)
        except re.error:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": (
                "kill-scope-guard (warn only, not blocked): the kill pattern is not a regex this guard can read.")}}))
            return 0
        others = [pid for pid in hit if pid not in own and not (len(session) >= 8 and session in table[pid][1])]
        if others:
            foreign.append((sel, others))
    if not foreign:
        return 0
    sel, others = foreign[0]
    sample = "; ".join("%s %s" % (p, table[p][1][:60]) for p in others[:3] if isinstance(p, int))
    print("BLOCKED (%s): `%s %s` would signal %s process(es) outside this session (%s). Kill only your own: find "
          "them with `pgrep -fl <pattern>`, check each is yours, then `kill <pid>`; or `pkill -P $$` for this "
          "shell's children." % (FLOOR, sel["tool"], sel["pattern"] or "", len(others), sample or others[0]),
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
