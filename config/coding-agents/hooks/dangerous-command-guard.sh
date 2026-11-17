#!/bin/bash
IFS= read -r -d '' GUARD <<'PY'
import json
import os
import posixpath
import re
import shlex
import sys
sys.path.insert(0, sys.argv.pop(1))
import approval_record
import data_delete
import own_worktree
import provider_cli
import psql_argv
import psql_commands
import psql_files
import shell_places
import shell_words
import sql_statements
ROOTS = {'~', '/Applications', '/lib', '/root', '*', '/System', '/etc', '${HOME}', '/Library', '/sbin', '/Volumes', '/home', '$HOME', '${HOME}/*', '/bin', '/*', '/Users', '.', '/usr', '..', '$HOME/*', '/var', '~/*', '/opt', '/'}

def protected(target):
    candidates = [target]
    if target == "$HOME" or target.startswith("$HOME/"):
        candidates.append(os.path.expanduser("~") + target[5:])
    elif target == "~" or target.startswith("~/"):
        candidates.append(os.path.expanduser("~") + target[1:])
    for candidate in candidates:
        candidate = posixpath.normpath(re.sub(r"/+", "/", candidate))
        if candidate.endswith("/*"):
            candidate = candidate[:-2] or "/"
        if (
            candidate in ROOTS
            or re.fullmatch(r"/(?:home|Users|Volumes)/[^/]+", candidate) is not None
            or re.fullmatch(r"~[^/]+", candidate) is not None
        ):
            return True
    return False
def unsafe_invocation(args, place=None):
    recursive = force = False
    options = True
    targets = []
    for arg in args:
        if options and arg == "--":
            options = False
        elif options and arg.startswith("--"):
            recursive |= len(arg) >= len("--r") and "--recursive".startswith(arg)
            force |= len(arg) >= len("--f") and "--force".startswith(arg)
        elif options and arg.startswith("-"):
            recursive |= any(flag in arg[1:] for flag in "rR")
            force |= "f" in arg[1:]
        else:
            targets.append(arg)
    return recursive and force and any(protected(target) and not (place and own_worktree.holds(place[0], place[1], target)) for target in targets)
def dangerous(command, place=None):
    try:
        return parse_dangerous(command, place)
    except Exception:
        return (
            re.search(
                r"(?<!\w)(?:\S*/)?rm\s+"
                r"(?=[^;\n]*-[A-Za-z]*[rR])(?=[^;\n]*-[A-Za-z]*f)"
                r"(?:-[A-Za-z]+\s+)+"
                r"(?:/(?:\*|(?:home|Users|Volumes)(?:/[^/\s;]+)?|root|etc|usr|var|opt|"
                r"bin|sbin|lib|System|Library|Applications)?/?|~[^/\s;]*(?:/\*)?|"
                r"\$HOME(?:/\*|/\.)?|\$\{HOME\}(?:/\*)?|\.\.?|\*)"
                r'(?=\s|[;\'"`]|$)',
                command,
            )
            is not None
        )
def parse_dangerous(command, place=None):
    if re.search(r"\bDROP\s+(DATABASE|TABLE)", command, re.I):
        return True
    command = re.sub(r"\$\{(HOME|USER)[^}]*\}", r"$\1", command)
    command = re.sub(
        r"(?:(?<!\S)\d+)?(?:&>>|&>|<<<|<<|>>|[<>]&|>\||[<>])"
        r'[ \t]*(?:"[^"\n]*"|\'[^\'\n]*\'|\\[^\n]|[^\s;&|<>()\x60\'"\\])+',
        "",
        command,
    )
    flattened = re.sub(r"[;&|(){}\x60\n]", " ; ", command)
    tokens = flattened.replace("'", " ").replace('"', " ").split()
    if own_worktree.moves(tokens):
        place = None
    for i, token in enumerate(tokens):
        token = token.lstrip("\\")
        if token == "rm" or token.endswith("/rm"):
            end = i + 1
            while end < len(tokens) and tokens[end] != ";":
                end += 1
            if unsafe_invocation(tokens[i + 1 : end], place):
                return True
    return False

FLOOR = "floor: destructive data with no approval on record"
UNPARSED_FLOOR = "DROP|TRUNCATE|launchctl[[:space:]]+disable"
DESTRUCTIVE_SQL = "(DROP[[:space:]]+(DATABASE|TABLE)|TRUNCATE[[:space:]]+(TABLE[[:space:]]+)?[^[:space:];])"
REF_COMMENT = re.compile(r"#\s*alex-approval:\s*(\S+)\s*")
def on_path(name):
    for folder in os.environ.get("PATH", os.defpath).split(os.pathsep):
        candidate = os.path.join(folder or ".", name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return True
    return False
def grep(pattern, text):
    read_end, write_end = os.pipe()
    try:
        pid = os.posix_spawnp(
            "grep",
            ["grep", "-qiE", pattern],
            os.environ,
            file_actions=[
                (os.POSIX_SPAWN_DUP2, read_end, 0),
                (os.POSIX_SPAWN_CLOSE, read_end),
                (os.POSIX_SPAWN_CLOSE, write_end),
            ],
        )
    except OSError:
        os.close(read_end)
        os.close(write_end)
        return 127
    os.close(read_end)
    data = (text + "\n").encode("utf-8", "surrogateescape")
    try:
        while data:
            data = data[os.write(write_end, data) :]
    except BrokenPipeError:
        pass
    finally:
        os.close(write_end)
    return os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1])
def refuse(why):
    sys.stderr.write(
        "BLOCKED: dangerous-command-guard %s, so this call is refused (the guard fails closed). "
        "Check jq and grep on PATH.\n" % why
    )
    sys.exit(2)
def allow():
    if os.environ.get("HOOK_FLOOR_RECEIPT"):
        sys.stdout.write("floor-ok dangerous-command-guard.sh\n")
    sys.exit(0)
def jq_raw(value):
    if value is None or value is False:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, ensure_ascii=False)
    return json.dumps(value)
def field(document, *path):
    for key in path:
        if document is None:
            return None
        if not isinstance(document, dict):
            raise TypeError("cannot index %s" % type(document).__name__)
        document = document.get(key)
    return jq_raw(document)
def documents(text):
    decoder = json.JSONDecoder()
    index, found = 0, []
    while True:
        while index < len(text) and text[index] in " \t\r\n":
            index += 1
        if index >= len(text):
            return found
        value, index = decoder.raw_decode(text, index)
        found.append(value)
def read_hook_input():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    except OSError as error:
        refuse("could not read the hook input (%s)" % error.__class__.__name__)
    if not on_path("jq"):
        refuse("could not read the hook input (jq exit 127)")
    try:
        parsed = documents(raw)
        commands = [field(doc, "tool_input", "command") for doc in parsed]
    except (ValueError, TypeError):
        refuse("could not read the hook input (jq exit 5)")
    try:
        cwds = [field(doc, "cwd") for doc in parsed]
    except TypeError:
        cwds = []
    command = "\n".join(text for text in commands if text is not None)
    cwd = "\n".join(text for text in cwds if text is not None)
    return command.replace("\x00", "").rstrip("\n"), cwd.replace("\x00", "").rstrip("\n")
def approved(cmd):
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
        elif c == "#" and i and cmd[i - 1].isspace():
            end = cmd.find("\n", i)
            end = len(cmd) if end < 0 else end
            if cmd[i:end].startswith("# alex-approval:") and not cmd[end:].strip():
                before = cmd[:i].rsplit("\n", 1)[-1]
                if before.strip():
                    comment = cmd[i:end]
            break
    m = REF_COMMENT.fullmatch(comment or "")
    return bool(m) and checked(m[1], "destructive", cmd[:i])
def checked(ref, action, cmd):
    reason = approval_record.refusal(ref, action, command=cmd)
    if reason:
        sys.stderr.write("approval record refused: " + reason + "\n")
    return not reason
def sql_text(cmd, cwd):
    found, bodies, unread, feeds, writes, loaded, inherit = [], [], [], [], [], set(), False
    start = cwd if cwd and os.path.isdir(cwd) else os.getcwd()
    cwds = {start}
    read, inline = psql_files.reader(cwds, unread, loaded)
    parts = list(psql_commands.commands(cmd, bodies, feeds))
    for words in parts:
        early = []
        while True:
            strings = []
            k = shell_words.command_word([(w, w, False, shlex.quote(w)) for w in words], strings)
            if k is not None and words[k].isdigit() and words[k + 1 : k + 2] and words[k + 1] in psql_argv.REDIRECTS:
                early.append(words[k])
                words = words[:k] + words[k + 1 :]
                continue
            if k is None or words[k] not in psql_argv.REDIRECTS or k + 1 >= len(words):
                break
            early += words[k : k + 2]
            words = words[:k] + words[k + 2 :]
        for text in strings:
            parts.extend(psql_commands.commands(psql_commands.restore(text), bodies, feeds))
        if k is None:
            writes += psql_argv.written("", early)
            continue
        plain = [psql_commands.restore(word) for word in words]
        name = os.path.basename(plain[k])
        if "command" in map(os.path.basename, plain[:k]) and {"-v", "-V"} & set(plain[:k]):
            continue
        cwds |= {shell_places.moved(name, plain[k:], {}, place) for place in cwds if place}
        inner = data_delete.nested(name, plain[k + 1 :])
        if inner is not None:
            parts.extend(psql_commands.commands(inner, bodies, feeds))
        args = early + words[k + 1 :] if name == "psql" else psql_argv.launched(name, words[k + 1 :])
        if name != "psql":
            writes += psql_argv.written(name, early + words[k + 1 : len(words) - len(args or [])])
        if args is None:
            continue
        fans = [w for w in plain[: k + 1] if os.path.basename(w) in psql_argv.FANS_OUT]
        if fans:
            unread.append("psql run by %s with arguments from its input" % os.path.basename(fans[0]))
        got, inherits, wrote = psql_argv.inspect(
            args, read, inline, unread, lambda w: psql_commands.body(w, bodies), "POSIXLY_CORRECT" in cmd
        )
        found += got
        writes += wrote
        inherit |= inherits
    if inherit and feeds:
        unread.append("%s: its stdin is fed through %s" % (psql_argv.FEEDS, " ".join(dict.fromkeys(feeds))))
    unread += ["%s (written in the same command)" % word for word in psql_argv.touches(writes, cwds, loaded)]
    return [piece.replace("\x00", "") for piece in found], unread
def main():
    cmd, cwd = read_hook_input()
    top = own_worktree.root(cwd)
    if dangerous(cmd, (top, os.path.realpath(cwd)) if top and cwd and "\n" not in cwd else None):
        sys.stderr.write("BLOCKED: Dangerous command requires explicit user approval.\n")
        sys.exit(2)
    if not cmd:
        allow()
    try:
        block = provider_cli.provider_block(cmd, cwd)
    except Exception:
        refuse("could not inspect provider argv")
    if block:
        sys.stderr.write("BLOCKED: %s\n" % block)
        sys.exit(2)
    try:
        sql, unread = sql_text(cmd, cwd)
    except Exception:
        match = grep(UNPARSED_FLOOR, cmd)
        if match not in (0, 1):
            refuse("could not match the command (grep exit %d: missing or failed)" % match)
        if match == 0:
            refuse("could not parse a kept-floor command")
        sys.stderr.write("WARNING: destructive-data guard could not parse executable argv; allowing command.\n")
        allow()
    match = grep(DESTRUCTIVE_SQL, re.sub(r"\s+", " ", "\n".join(sql) + "\n" + sql_statements.statements(sql)))
    if match == 1 and sql_statements.deletes_all(sql):
        match = 0
    if match not in (0, 1):
        refuse("could not match the command (grep exit %d: missing or failed)" % match)
    if (match == 0 or unread) and not approved(cmd):
        if unread:
            sys.stderr.write(
                "dangerous-command-guard: psql would run SQL it cannot see or read: %s. Pass the SQL with "
                "psql -c '<sql>' or psql -f <file> (a readable file nothing in this command writes).\n" % "; ".join(unread)
            )
        sys.stderr.write(
            "BLOCKED: Dangerous command requires explicit user approval (%s). Approved: append "
            "' # alex-approval: ~/.open-factory/approvals/alex-<topic>-2026-<date>.md' "
            "(Evidence: the T3 message Alex typed, Quote: his words, Covers: destructive).\n" % FLOOR
        )
        sys.exit(2)
    allow()

main()
PY
shopt -s execfail
exec python3 -I -S -c "$GUARD" "$(cd "$(dirname "$0")" && pwd -P)" "$@"
echo "BLOCKED: dangerous-command-guard could not start python3, so this call is refused (the guard fails closed)." >&2
exit 2
