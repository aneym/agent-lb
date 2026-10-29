#!/usr/bin/env python3

import ctypes
import json
import os
import re
import shlex
import sys

FLOOR = "floor: other agents' running work (a kill must stay inside this session)"
WRAPPERS = {"sudo", "env", "command", "exec", "nohup", "time", "nice", "caffeinate", "stdbuf", "timeout"}
SHELLS = {"bash", "sh", "zsh", "dash"}
KEYWORDS = {"if", "then", "do", "while", "until", "else", "elif", "!", "{", "}", "fi", "done"}
OPERATORS = "<>;&|()"
HEREDOC = re.compile(r"(?<!<)<<(?!<)-?\s*(\\?)(['\"]?)([A-Za-z0-9_.-]+)\2")

VALUED = set("FGgPstUu")
LSOF_T = r"\blsof\b[^|;&\n]*\s-[A-Za-z]*t[A-Za-z]*\b"
PORT_KILL = (
    re.compile(LSOF_T + r"[^|;&\n]*\|\s*(?:sudo\s+)?(?:xargs\b[^|;&\n]*\bkill\b|kill\b)"),
    re.compile(r"\bkill\b[^|;&\n]*(?:\$\(|`)\s*" + LSOF_T),
)

T3_CODEX_STOP = re.compile(
    r"\b(?:systemctl\b[^;&|\n]*\b(?:stop|kill|restart|try-restart|reload-or-restart|mask|freeze)\b|pkill\b|killall\b)"
    r"[^;&|\n]*(?:t3code-codex-|codex[ .*]+app-server)"
)


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
            ends += [
                (m.group(3), not (m.group(1) or m.group(2)), first_word(line[: m.start()]) in SHELLS, [])
                for m in HEREDOC.finditer(line)
                if m.start() in free
            ]
        quote = next_quote
    if ends:
        raise Doubt
    return "\n".join(out), "\n".join(live), shell


def substitutions(text):

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
                body, nxt = text[k + 1 : end], end
            else:
                depth, j = 1, k + 2
                while j < len(text) and depth:
                    depth += {"(": 1, ")": -1}.get(text[j], 0)
                    j += 1
                if depth:
                    raise Doubt
                body, nxt = text[k + 2 : j - 1], j - 1
            head = re.split(r"[;&|\n]", text[:k])[-1]
            found.append((body, re.search(r"(^|\s)(\S*/)?kill(\s|$)", head) is not None))
            k = nxt
        k += 1
    return found


def pipelines(text):

    if any(0xE000 <= ord(c) < 0xE000 + len(OPERATORS) for c in text):
        raise Doubt
    free, _ = unquoted(text, None)
    text = "".join(
        ("\n ; " if c == "\n" else c) if k in free else chr(0xE000 + OPERATORS.index(c)) if c in OPERATORS else c
        for k, c in enumerate(text)
    )
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
            rest = [a for a in args[i + 1 :] if not a.startswith(("-", "+"))]
            return rest[0] if rest else None
    return None


def selector(argv):

    name = os.path.basename(argv[0])
    sel = {"full": False, "exact": name == "killall", "ignore": False, "parents": None, "pattern": None, "tool": name}
    args, i = argv[1:], 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            rest = args[i + 1 :]
            sel["pattern"] = rest[-1] if rest else None
            break
        if arg.startswith("-") and len(arg) > 1 and not re.fullmatch(r"-\d+|-(SIG)?[A-Z]{2,}[0-9]*", arg):
            flags = arg[1:]
            for j, flag in enumerate(flags):
                if flag in VALUED and name != "killall":
                    value = flags[j + 1 :] or (args[i + 1] if i + 1 < len(args) else None)
                    if not flags[j + 1 :]:
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
            if name == "kill":
                args = argv[1:]
                if args and args[0] in ("-l", "-L"):
                    continue
                if args and args[0] in ("-s", "-n"):
                    args = args[2:]
                elif args and re.fullmatch(r"-(?:\d+|(?:SIG)?[A-Z][A-Z0-9]+)", args[0]):
                    args = args[1:]
                if args and args[0] == "--":
                    args = args[1:]
                if not args or any(not a.isdigit() or int(a) <= 1 for a in args):
                    raise Doubt
                out.append({"pids": [int(a) for a in args], "pattern": None, "parents": None, "tool": "kill"})
            elif name in ("pkill", "killall"):
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


TTY_PIDS = set()
NODEV = 0xFFFFFFFF


class BsdInfo(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_uint32),
        ("status", ctypes.c_uint32),
        ("xstatus", ctypes.c_uint32),
        ("pid", ctypes.c_uint32),
        ("ppid", ctypes.c_uint32),
        ("ids", ctypes.c_uint32 * 7),
        ("comm", ctypes.c_char * 16),
        ("name", ctypes.c_char * 32),
        ("nfiles", ctypes.c_uint32),
        ("pgid", ctypes.c_uint32),
        ("pjobc", ctypes.c_uint32),
        ("tdev", ctypes.c_uint32),
        ("tpgid", ctypes.c_uint32),
        ("nice", ctypes.c_int32),
        ("start", ctypes.c_uint64 * 2),
    ]


class ShortInfo(ctypes.Structure):
    _fields_ = [
        ("pid", ctypes.c_uint32),
        ("ppid", ctypes.c_uint32),
        ("pgid", ctypes.c_uint32),
        ("status", ctypes.c_uint32),
        ("comm", ctypes.c_char * 16),
        ("rest", ctypes.c_uint32 * 8),
    ]


def command_of(libc, pid):
    mib = (ctypes.c_int * 3)(1, 49, pid)
    size = ctypes.c_size_t(0)
    if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) or size.value <= 4:
        return None
    raw = ctypes.create_string_buffer(size.value)
    if libc.sysctl(mib, 3, raw, ctypes.byref(size), None, 0):
        return None
    data = raw.raw[: size.value]
    argc, rest = int.from_bytes(data[:4], sys.byteorder), data[4:]
    rest = rest[max(rest.find(b"\0"), 0) :].lstrip(b"\0")
    words = rest.split(b"\0")[:argc]
    return " ".join(word.decode(errors="replace") for word in words) or None


def linux_table():
    table = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", "rb") as handle:
                stat = handle.read().decode(errors="replace")
            with open(f"/proc/{entry}/cmdline", "rb") as handle:
                argv = handle.read().rstrip(b"\0").replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        fields = stat[stat.rfind(")") + 2 :].split()
        if int(fields[4]):
            TTY_PIDS.add(int(entry))
        table[int(entry)] = (int(fields[1]), argv or stat[stat.find("(") + 1 : stat.rfind(")")])
    return table


def process_table():
    if sys.platform == "linux":
        return linux_table()
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    libc = ctypes.CDLL("/usr/lib/libc.dylib", use_errno=True)
    libc.sysctl.argtypes = [
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.c_void_p,
        ctypes.c_size_t,
    ]
    count = libproc.proc_listallpids(None, 0)
    if count <= 0:
        raise OSError("proc_listallpids failed")
    pids = (ctypes.c_int * (count + 512))()
    count = libproc.proc_listallpids(pids, ctypes.sizeof(pids))
    if count <= 0:
        raise OSError("proc_listallpids failed")
    table = {}
    for pid in pids[:count]:
        if pid <= 0:
            continue
        info, short = BsdInfo(), ShortInfo()
        if libproc.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info)) == ctypes.sizeof(info):
            ppid, comm = info.ppid, info.comm
            if info.tdev != NODEV:
                TTY_PIDS.add(pid)
        elif libproc.proc_pidinfo(pid, 13, 0, ctypes.byref(short), ctypes.sizeof(short)) == ctypes.sizeof(short):
            ppid, comm = short.ppid, short.comm
        else:
            continue
        table[pid] = (ppid, command_of(libc, pid) or comm.decode(errors="replace"))
    return table


def session_root(table, start):

    pid, seen = start, set()
    while pid in table and pid not in seen and pid > 1:
        seen.add(pid)
        command = table[pid][1]
        exe = command.split()[0] if command.split() else ""
        if os.path.basename(exe) in {"claude", "codex"} or re.search(
            r"(^|/)claude(\s|$)|@anthropic-ai/claude-code", command
        ):
            return pid
        pid = table[pid][0]
    return start


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
        subject = command if sel["full"] else os.path.basename(words[0]).lstrip("-") if words else ""
        found = rx.fullmatch(subject) if sel["exact"] else rx.search(subject)
        if found:
            hit.append(pid)
    return hit


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    stop = T3_CODEX_STOP.search(command)
    if stop:
        print(
            "BLOCKED (%s): `%s` stops a shared T3 Codex app-server and fails every turn on it. Leave it to T3; "
            "report a runaway one to the t3-nightly thread with t3_thread_send." % (FLOOR, stop.group(0)[:80]),
            file=sys.stderr,
        )
        return 2
    if not re.search(r"\b(kill|pkill|killall|pgrep)\b", command):
        return 0
    try:
        selections = kills(command)
    except Doubt:
        selections = None
    if selections == []:
        return 0
    if (
        selections is None
        or any("pids" not in s and s["pattern"] is None and s["parents"] is None for s in selections)
        or any(s["pattern"] and re.search(r"[$`]", s["pattern"]) for s in selections)
    ):
        return 0
    try:
        table = process_table()
    except (OSError, AttributeError, ValueError):
        return 0
    start = os.getppid()
    caller = os.environ.get("HOOK_CALLER_PID", "")
    if caller.isdigit() and "hookd.py" in table.get(start, (0, ""))[1]:
        start = int(caller)
    own = descendants(table, session_root(table, start))
    foreign = []
    live = set()
    for pid, (_, argv) in table.items():
        words = argv.split()
        exe = os.path.basename(words[0]) if words else ""
        if exe in {"claude", "codex"} or re.search(
            r"@anthropic-ai/claude-code|(?:seat-exec|seat-run|agent-lb)(?:\s|/|$)|(?:^|[/\s])t3\s+serve(?:\s|$)", argv
        ):
            live.update(descendants(table, pid))
    for sel in selections:
        if sel["parents"] is not None:
            parents = [p for p in re.split(r"[ ,]", sel["parents"]) if p]
            if all(p in ("$$", "$PPID") or (p.isdigit() and int(p) in own) for p in parents):
                continue
            if not all(p.isdigit() for p in parents):
                continue
            hit = [pid for pid, (ppid, _) in table.items() if str(ppid) in parents]
        elif "pids" in sel:
            hit = sel["pids"]
        else:
            try:
                hit = matches(sel, table)
            except re.error:
                continue
        others = []
        for pid in hit:
            if pid not in table:
                continue
            ppid, argv = table[pid]
            executable = os.path.basename(argv.split()[0]) if argv.split() else ""
            gui = (
                executable not in {"Python", "python", "python3", "node", "lume"}
                and bool(re.search(r"\.app/Contents/(?:MacOS|Frameworks/[^ ]*\.app/Contents/MacOS)/", argv))
                or os.path.basename(argv.split()[0]) in {"Finder", "Dock", "WindowServer", "loginwindow"}
            )
            shared = bool(
                re.search(
                    r"(?:^|[/\s])(?:t3\s+(?:serve|__service-launcher)(?:\s|$)|agent[-_]lb(?:[./\s]|$)|unblock(?:[./\s]|$)|postgres(?:\s|$)|mysqld(?:\s|$)|redis-server(?:\s|$))",
                    argv,
                    re.I,
                )
            )
            words = argv.split()
            interactive_shell = executable.lstrip("-") in SHELLS and (
                executable.startswith("-")
                or pid in TTY_PIDS
                or any(
                    arg.startswith("-") and not arg.startswith("--") and set(arg[1:]) & {"i", "l"} for arg in words[1:]
                )
            )
            if gui or shared or executable in {"launchd", "WindowServer", "loginwindow", "Dock", "Finder"}:
                others.append(pid)
        if others:
            foreign.append((sel, others))
    if not foreign:
        return 0
    sel, others = foreign[0]
    sample = "; ".join("%s %s" % (p, table[p][1][:60]) for p in others[:3] if isinstance(p, int))
    print(
        "BLOCKED (%s): `%s %s` would signal %s process(es) outside this session (%s). Kill only your own: find "
        "them with `pgrep -fl <pattern>`, check each is yours, then `kill <pid>`; or `pkill -P $$` for this "
        "shell's children." % (FLOOR, sel["tool"], sel["pattern"] or "", len(others), sample or others[0]),
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    code = main()
    if code == 0 and os.environ.get("HOOK_FLOOR_RECEIPT"):
        print("floor-ok kill-scope-guard.py")
    sys.exit(code)
