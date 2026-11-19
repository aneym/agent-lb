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
    target = re.sub(r"\$\{(HOME|USER)[^}]*\}", r"$\1", target)
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
RM_ROOT = re.compile(
    r"(?<!\w)(?:\S*/)?rm\s+"
    r"(?=[^;\n]*-[A-Za-z]*[rR])(?=[^;\n]*-[A-Za-z]*f)"
    r"(?:-[A-Za-z]+\s+)+"
    r"(?:/(?:\*|(?:home|Users|Volumes)(?:/[^/\s;]+)?|root|etc|usr|var|opt|"
    r"bin|sbin|lib|System|Library|Applications)?/?|~[^/\s;]*(?:/\*)?|"
    r"\$HOME(?:/\*|/\.)?|\$\{HOME\}(?:/\*)?|\.\.?|\*)"
    r'(?=\s|[;\'"`]|$)'
)
def place_of(command, cwd):
    top = own_worktree.root(cwd)
    if not (top and cwd and "\n" not in cwd):
        return None
    tokens = re.sub(r"[;&|(){}\x60\n]", " ; ", command).replace("'", " ").replace('"', " ").split()
    return None if own_worktree.moves(tokens) else (top, os.path.realpath(cwd))

FLOOR = "floor: destructive data with no approval on record"
DESTRUCTIVE_SQL = re.compile(r"(DROP\s+(DATABASE|TABLE)|TRUNCATE\s+(TABLE\s+)?(ONLY\s+)?[\"`\[]?[A-Za-z_][\w.$]*(?![\w-]))", re.I)
DB_ACCESS = re.compile(
    r"sqlite|DatabaseSync|psycopg|asyncpg|pg8000|\bpsql\b|postgres(ql)?:|mysql|mariadb|duckdb|libsql|turso|\bd1\b|"
    r"wrangler|prisma|knex|drizzle|sequelize|typeorm|\bpg\b|\.connect\(|createClient\(|new\s+Pool\(|"
    r"\.execute(script|many)?\(|\.exec\(|\.query\(|\.prepare\(|\.db\b|/sql\b|/query\b",
    re.I,
)
RUNS_INPUT = data_delete.SHELLS | {"ssh", "xargs", "parallel", "eval", "busybox", "psql"}
SQL_CLIENTS = {
    "sqlite3", "mysql", "mariadb", "duckdb", "wrangler", "turso", "sqlcmd", "clickhouse",
    "clickhouse-client", "pgcli", "mycli", "litecli", "usql", "rails-one-sql",
}
WIPE = (
    "BLOCKED: Dangerous command requires explicit user approval: rm -rf of a root or home directory. "
    "Delete the named paths inside it with agent-rm instead.\n"
)
TEXT_TOOLS = psql_argv.READ_ONLY | {
    "git", "gh", "t3", "tee", "sort", "uniq", "cut", "tr", "sed", "awk", "jq", "egrep", "fgrep", "more", "nl",
    "agent-request", "agent-grants",
}
INTERPRETERS = {"python", "python3", "node", "perl", "ruby", "bun", "deno", "tsx"}
INLINE = {"-", "-c", "-e", "-p", "-E", "--eval", "--print"}
FILE_WORD = re.compile(r"[\w.-]+\.[A-Za-z]\w{0,4}")
RUNNERS = INTERPRETERS | {"npx", "npm", "pnpm", "bunx", "yarn", "make", "just", "wrangler"}
UNWRAPS = data_delete.SHELLS | {"busybox", "eval", "alias", "ssh"}
FILE_SQL = re.compile(r"(?:^|[\s;])(?:\.read|source|\\\.|\\i)\s+('[^']*'|\"[^\"]*\"|[^\s;]+)", re.I)
ROUTE = (
    "Way through: cite a destructive-data record under ~/.open-factory/approvals/ by appending "
    "' # alex-approval: ~/.open-factory/approvals/alex-<topic>-2026-<date>.md' to the command, and check the record "
    "first with: python3 ~/.claude/hooks/approval_record.py <record> --action destructive --command "
    "'<the command without that comment>'\n"
)
REF_COMMENT = re.compile(r"#\s*alex-approval:\s*(\S+)\s*")
def allow():
    if os.environ.get("HOOK_FLOOR_RECEIPT"):
        sys.stdout.write("floor-ok dangerous-command-guard.sh\n")
    sys.exit(0)
def let_through(why):
    sys.stderr.write("WARNING: dangerous-command-guard %s, so it lets this call through.\n" % why)
    allow()
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
        parsed = documents(raw)
        commands = [field(doc, "tool_input", "command") for doc in parsed]
    except (OSError, ValueError, TypeError) as error:
        let_through("could not read the hook input (%s)" % error.__class__.__name__)
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
def destructive(pieces):
    text = re.sub(r"\s+", " ", "\n".join(pieces) + "\n" + sql_statements.statements(pieces))
    return bool(DESTRUCTIVE_SQL.search(text)) or sql_statements.deletes_all(pieces)
def sql_files(words, cwds, seen):
    texts = []
    for word in words:
        for name in [word.split("=", 1)[-1]] + [m[1].strip("'\"") for m in FILE_SQL.finditer(word)]:
            for base in sorted(c for c in cwds if c):
                path = os.path.realpath(os.path.join(base, os.path.expanduser(name))) if name else ""
                text = psql_files.file_text(path) if path and path not in seen else None
                if text is not None:
                    seen.add(path)
                    texts += [text] + sql_files([text], cwds, seen)
    return texts
def redirected(words, cwds):
    return sql_files([w for i, w in enumerate(words[1:]) if words[i] in ("<", "<>")], cwds, set())
def scan(cmd, cwd, place):
    found, bodies, unread, feeds, writes, loaded, inherit, wiped = [], [], [], [], [], set(), False, False
    filed, unlisted, ends, seen = [], [], [], {}
    start = cwd if cwd and os.path.isdir(cwd) else os.getcwd()
    cwds = {start}
    read, inline = psql_files.reader(cwds, unread, loaded)
    parts = list(psql_commands.commands(cmd, bodies, feeds, ends))
    for index, words in enumerate(parts):
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
            parts.extend(psql_commands.commands(psql_commands.restore(text), bodies, feeds, ends))
        if k is None:
            writes += psql_argv.written("", early)
            continue
        plain = [psql_commands.restore(word) for word in words]
        name = os.path.basename(plain[k])
        if "command" in map(os.path.basename, plain[:k]) and {"-v", "-V"} & set(plain[:k]):
            continue
        cwds |= {shell_places.moved(name, plain[k:], {}, place) for place in cwds if place}
        if name == "rm" and unsafe_invocation(plain[k + 1 :], place):
            wiped = True
        given = [psql_commands.restore(w) for w in early] + plain[k + 1 :]
        docs = [doc[0] for doc in (psql_commands.body(w, bodies) for w in early + words[k + 1 :]) if doc]
        seen[index] = (name, [" ".join(given)] + docs + redirected(given, cwds), plain[k])
        inner = data_delete.nested(name, plain[k + 1 :])
        if name in SQL_CLIENTS:
            found.append(" ".join(plain[k + 1 :]))
            found += [" ".join(map(psql_commands.restore, early))] + docs
            filed += sql_files(given + docs, cwds, set())
        elif name != "psql" and name not in TEXT_TOOLS and not (name in UNWRAPS and inner is not None):
            plainargs = [w for w in plain[k + 1 :] if not w.startswith("-") or w in INLINE]
            inline = name in INTERPRETERS and (not plainargs or plainargs[0] in INLINE)
            unlisted.append((name, [" ".join(given)] + docs + redirected(given, cwds), inline))
        if inner is not None:
            parts.extend(psql_commands.commands(inner, bodies, feeds, ends))
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
    hits = ["destructive SQL read from a file a SQL client runs"] if destructive(filed) else []
    hits += [
        "destructive SQL passed to %s, which is not a SQL client this guard knows" % name
        for name, pieces, inline in unlisted
        if destructive(pieces) and (not inline or DB_ACCESS.search("\n".join(pieces)))
    ]
    staged = [
        index
        for index, (name, pieces, _) in seen.items()
        if name not in SQL_CLIENTS and any(destructive([piece]) and DB_ACCESS.search(piece) for piece in pieces)
    ]
    files = {
        os.path.basename(w)
        for index, (_, pieces, _) in seen.items()
        if staged and index >= staged[0] and (index in staged or ">" in pieces[0].split())
        for w in pieces[0].split()
        if FILE_WORD.fullmatch(os.path.basename(w))
    }
    hits += [
        "destructive SQL written and then run by %s in the same command" % name
        for index, (name, pieces, word) in seen.items()
        if staged
        and index > staged[0]
        and (name in RUNNERS | RUNS_INPUT or "/" in word or name in SQL_CLIENTS)
        and (name in SQL_CLIENTS or files & {os.path.basename(w) for w in [word] + pieces[0].split()})
    ][:1]
    for index, (name, pieces, _) in seen.items():
        if name in TEXT_TOOLS - SQL_CLIENTS - RUNS_INPUT:
            continue
        start = index
        while 0 < start <= len(ends) and "|" in ends[start - 1].replace("||", ""):
            start -= 1
        upstream = [piece for i in range(start, index) for piece in seen.get(i, ("", [], ""))[1]]
        if upstream and destructive(upstream + sql_files(upstream, cwds, set())):
            hits.append("destructive SQL fed through a pipe to %s" % name)
    return [piece.replace("\x00", "") for piece in found], unread, wiped, hits
def main():
    cmd, cwd = read_hook_input()
    if not cmd:
        allow()
    try:
        block = provider_cli.provider_block(cmd, cwd)
    except Exception:
        block = ""
        sys.stderr.write("WARNING: dangerous-command-guard could not inspect provider argv, so it skips that check.\n")
    if block:
        sys.stderr.write("BLOCKED: %s\n" % block)
        sys.exit(2)
    try:
        sql, unread, wiped, hits = scan(cmd, cwd, place_of(cmd, cwd))
    except Exception:
        if RM_ROOT.search(cmd):
            sys.stderr.write(WIPE)
            sys.exit(2)
        let_through("could not parse the command")
    if wiped:
        sys.stderr.write(WIPE)
        sys.exit(2)
    named = destructive(sql)
    if (named or unread or hits) and not approved(cmd):
        for why in hits:
            sys.stderr.write("dangerous-command-guard: %s.\n" % why)
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
        if named or hits:
            sys.stderr.write(ROUTE)
        sys.exit(2)
    allow()

main()
PY
shopt -s execfail
exec python3 -I -S -c "$GUARD" "$(cd "$(dirname "$0")" && pwd -P)" "$@"
echo "WARNING: dangerous-command-guard could not start python3, so it lets this call through." >&2
if [ -n "${HOOK_FLOOR_RECEIPT:-}" ]; then printf 'floor-ok %s\n' dangerous-command-guard.sh; fi
exit 0
