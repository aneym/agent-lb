#!/usr/bin/env python3
import json
import os
import re
import shlex
import sys
from collections import deque

RECEIPT_ENV = "HOOK_FLOOR_RECEIPT"
RECEIPT = "floor-ok merge-gate-guard.py"
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish", "ssh"}
HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
PATH = r"(?:[^\s/;&|()`\"']*/)*"


def base(word):
    return word.rsplit("/", 1)[-1]


def feeds_shell(line):
    try:
        words = shlex.split(HEREDOC.sub(" ", line), comments=False)
    except ValueError:
        return True
    return any(base(w) in SHELLS or w in {"eval", "source", "."} for w in words)


def scan(line, state):
    names, i, n = [], 0, len(line)
    while i < n:
        c, frame = line[i], state[1][-1]
        if state[0]:
            if c == "\\" and state[0] == "$'":
                i += 1
            elif c == "'":
                state[0] = None
        elif c == "\\":
            i += 1
        elif c == "'" and not frame[0]:
            state[0] = "$'" if i and line[i - 1] == "$" else "'"
        elif c == '"':
            frame[0] = not frame[0]
        elif line.startswith("$(", i):
            state[1].append([False, 0])
            i += 1
        elif frame[0]:
            pass
        elif c == "(":
            frame[1] += 1
        elif c == ")":
            if frame[1]:
                frame[1] -= 1
            elif len(state[1]) > 1:
                state[1].pop()
        elif c == "#" and (i == 0 or line[i - 1] in " \t;&|()"):
            return names, i
        elif line.startswith("<<<", i):
            i += 2
        elif line.startswith("<<", i):
            match = HEREDOC.match(line, i)
            if match:
                names.append(match.group(2))
                i = match.end() - 1
        i += 1
    return names, n


def uncomment(command):
    state, lines = [None, [[False, 0]]], []
    for line in command.split("\n"):
        lines.append(line[: scan(line, state)[1]])
    return "\n".join(lines)


def split_heredocs(command):
    out, run, pending, state = [], [], deque(), [None, [[False, 0]]]
    for line in command.split("\n"):
        if pending:
            if line.strip() == pending[0][0]:
                pending.popleft()
            elif pending[0][1]:
                run.append(line)
            continue
        out.append(line)
        opened = scan(line, state)[0]
        if opened:
            shell = feeds_shell(line)
            pending.extend((name, shell) for name in opened)
    return "\n".join(out), "\n".join(run)


WRAPPERS = {
    "command",
    "exec",
    "time",
    "nohup",
    "env",
    "sudo",
    "caffeinate",
    "timeout",
    "xargs",
    "nice",
    "doas",
    "do",
    "then",
    "else",
    "elif",
    "if",
    "while",
    "until",
    "!",
    "{",
}
ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "|&", ";;"}
GH_VALUE_FLAGS = {"-R", "--repo", "--hostname"}
FALLBACK = re.compile(r"(^|[;&|(\s])" + PATH + r"gh\s+(?:(?:-R|--repo)[= ]\S+\s+)*pr\s+merge\b")
LOOSE = re.compile(r"(^|[;&|(\s`\"])" + PATH + r"gh\s+(?:(?:-R|--repo)[= ]\S+\s+)*pr\s+merge\b")
MAX_BODIES = 32
MAX_PARSE = 20000
MAX_DEPTH = 6
PIPE_TO_SHELL = re.compile(r"\|\s*" + PATH + r"(?:bash|sh|zsh|dash|ksh|fish)\b(?!\s+-c)")
MESSAGE = (
    "BLOCKED: direct `gh pr merge` skips the merge gate. agent-rails lands with "
    "`python3 scripts/studio_merge.py merge <branch>`; other repos fast-forward push to origin/main; "
    "Open Factory gated repos queue with `of gate queue <pr>`. "
    "`gh pr merge <pr> --disable-auto` (dequeue) is still allowed."
)


def close_of(command, i):
    single, double, depth, n = False, False, 1, len(command)
    while i < n:
        c = command[i]
        if single:
            single = c != "'"
        elif c == "\\":
            i += 1
        elif c == "'" and not double:
            single = True
        elif c == '"':
            double = not double
        elif command.startswith("$(", i):
            i = close_of(command, i + 2)
        elif c == "`":
            close = command.find("`", i + 1)
            i = n if close < 0 else close
        elif not double and c == "(":
            depth += 1
        elif not double and c == ")":
            depth -= 1
            if not depth:
                return i
        i += 1
    return n


def substitutions(command):
    out, single, double, i, n = [], False, False, 0, len(command)
    while i < n:
        c = command[i]
        if c == "\\" and not single:
            i += 2
            continue
        if c == '"' and not single:
            double = not double
        elif c == "'" and not double:
            single = not single
        elif not single and c == "`":
            close = command.find("`", i + 1)
            if close < 0:
                break
            out.append(command[i + 1 : close])
            i = close
        elif not single and command.startswith("$(", i):
            close = close_of(command, i + 2)
            out.append(command[i + 2 : close])
            i = close
        i += 1
    return out


def unsingle(command):
    out, single, frames, i, n = [], False, [[False, 0]], 0, len(command)
    while i < n:
        c, frame = command[i], frames[-1]
        if single:
            single = c != "'"
            out.append(" ")
        elif c == "\\":
            out.append(command[i : i + 2])
            i += 1
        elif c == "'" and not frame[0]:
            single = True
            out.append(" ")
        else:
            if c == '"':
                frame[0] = not frame[0]
            elif command.startswith("$(", i):
                frames.append([False, 0])
                out.append("$(")
                i += 2
                continue
            elif c == "(" and not frame[0]:
                frame[1] += 1
            elif c == ")" and not frame[0]:
                if frame[1]:
                    frame[1] -= 1
                elif len(frames) > 1:
                    frames.pop()
            out.append(c)
        i += 1
    return "".join(out)


ANSI_ESCAPES = {
    "a": "\a",
    "b": "\b",
    "e": "\x1b",
    "E": "\x1b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
    "'": "'",
    '"': '"',
    "?": "?",
}
ANSI_NUMERIC = re.compile(r"x([0-9a-fA-F]{1,2})|u([0-9a-fA-F]{1,4})|U([0-9a-fA-F]{1,8})|([0-7]{1,3})")
ANSI_SPECIAL = re.compile(r"[\\'\"$()]")


def ansi_body(command, i):
    out, n, nul = [], len(command), False
    while i < n:
        match = ANSI_SPECIAL.search(command, i)
        end = match.start() if match else n
        if not nul:
            out.append(command[i:end])
        i = end
        if i == n or command[i] == "'":
            break
        if command[i] == "\\" and i + 1 < n:
            numeric = ANSI_NUMERIC.match(command, i + 1)
            if numeric:
                hexa, uni, wide, octal = numeric.groups()
                code = int(hexa or uni or wide, 16) if not octal else int(octal, 8) & 0xFF
                decoded = chr(code) if code < 0x110000 else ""
                i = numeric.end()
            elif command[i + 1] == "c" and i + 2 < n:
                ctrl = command[i + 2]
                decoded = "\x7f" if ctrl == "?" else chr(ord(ctrl.upper()) & 0x1F)
                i += 4 if ctrl == "\\" and command.startswith("\\", i + 3) else 3
            else:
                decoded = ANSI_ESCAPES.get(command[i + 1], "\\" + command[i + 1])
                i += 2
            if decoded == "\0":
                nul = True
            elif not nul:
                out.append(decoded)
            continue
        if not nul:
            out.append(command[i])
        i += 1
    return "".join(out), i


def deansi(command):
    if "$'" not in command and '$"' not in command:
        return command
    out, single, frames, i, n = [], False, [[False, 0]], 0, len(command)
    while i < n:
        match = ANSI_SPECIAL.search(command, i)
        end = match.start() if match else n
        out.append(command[i:end])
        i = end
        if i == n:
            break
        c, frame = command[i], frames[-1]
        if single:
            single = c != "'"
        elif c == "\\":
            out.append(command[i : i + 2])
            i += 2
            continue
        elif command.startswith("$'", i) and not frame[0]:
            text, close = ansi_body(command, i + 2)
            out.append(shlex.quote(text))
            i = close + 1
            continue
        elif command.startswith('$"', i) and not frame[0]:
            frame[0] = True
            out.append('"')
            i += 2
            continue
        elif c == "'" and not frame[0]:
            single = True
        elif c == '"':
            frame[0] = not frame[0]
        elif command.startswith("$(", i):
            frames.append([False, 0])
            out.append("$(")
            i += 2
            continue
        elif c == "(" and not frame[0]:
            frame[1] += 1
        elif c == ")" and not frame[0]:
            if frame[1]:
                frame[1] -= 1
            elif len(frames) > 1:
                frames.pop()
        out.append(c)
        i += 1
    return "".join(out)


def loose_merge(command):
    text = unsingle(command)
    return bool(LOOSE.search(text)) and "--disable-auto" not in text


def simple_commands(command):
    command = uncomment(command).replace("\\\n", "")
    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    lexer.commenters = ""
    current = []
    for token in lexer:
        if token in SEPARATORS:
            if current:
                yield current
            current = []
        else:
            current.append(token)
    if current:
        yield current


def command_head(words):
    i = 0
    while i < len(words) and ASSIGN.fullmatch(words[i]):
        i += 1
    if i >= len(words):
        return None
    if base(words[i]) not in WRAPPERS:
        return i
    for j in range(i + 1, len(words)):
        if base(words[j]) in SHELLS or base(words[j]) in {"gh", "eval"}:
            return j
    return None


def is_direct_merge(words, depth=0):
    i = command_head(words)
    if i is None:
        return False
    head = base(words[i])
    rest = words[i + 1 :]
    if head in SHELLS or head == "eval":
        inner = [
            rest[j + 1]
            for j, w in enumerate(rest[:-1])
            if w in {"-c", "<<<"} or w.startswith("-") and "c" in w[1:] and not w.startswith("--")
        ]
        if head == "eval":
            inner = [" ".join(rest)]
        if head == "ssh":
            starts = [j for j, w in enumerate(rest) if base(w) in {"gh", "eval"} | SHELLS]
            inner = [" ".join(rest[starts[0] :])] if starts else []
            inner += [w for w in rest if " " in w]
        return any(blocked(text, depth + 1) for text in inner)
    if head != "gh":
        return False
    positional, j = [], 0
    while j < len(rest) and len(positional) < 2:
        w = rest[j]
        if w in GH_VALUE_FLAGS:
            j += 2
            continue
        if not w.startswith("-"):
            positional.append(w)
        j += 1
    return positional == ["pr", "merge"] and not {
        "--disable-auto",
        "--help",
        "-h",
    } & set(rest)


def blocked(command, depth=0):
    command, shell_bodies = split_heredocs(command)
    if shell_bodies and blocked(shell_bodies, depth + 1):
        return True
    command = deansi(uncomment(command))
    if len(command) > MAX_PARSE or depth > MAX_DEPTH:
        return loose_merge(command)
    bodies = substitutions(command)
    if bodies and loose_merge(command):
        return True
    if len(bodies) <= MAX_BODIES and any(blocked(body, depth + 1) for body in bodies):
        return True
    if PIPE_TO_SHELL.search(command):
        unquoted = command.replace("'", " ").replace('"', " ")
        if FALLBACK.search(unquoted) and "--disable-auto" not in unquoted:
            return True
    try:
        return any(is_direct_merge(words, depth) for words in simple_commands(command))
    except ValueError:
        return bool(FALLBACK.search(command)) and "--disable-auto" not in command


def merged(command):
    if "merge" not in command:
        joined = command.replace("\\\n", "").translate(str.maketrans("", "", "'$\"\\"))
        if "merge" not in joined and not ("$'" in command and "\\" in command):
            return False
    try:
        return blocked(command)
    except RecursionError:
        return loose_merge(command)


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except ValueError:
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") not in (None, "Bash"):
        return 0
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not command:
        return 0
    if os.environ.get(RECEIPT_ENV):
        print(RECEIPT)
    if not merged(command):
        return 0
    print(MESSAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
