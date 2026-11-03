#!/bin/bash
IFS= read -r -d '' GUARD <<'PY'
import json
import os
import re
import shlex
import sys

sys.path.insert(0, sys.argv.pop(1))
import approval_record
import data_delete
import psql_argv
import psql_commands
import psql_files
import shell_places
import shell_words
import sql_statements

FLOOR = "floor: destructive data with no approval on record"
PROVIDERS = {
    "muse": ("Muse Code", ("META_API_KEY",)),
    "railway": ("Railway", ("RAILWAY_TOKEN", "RAILWAY_API_TOKEN")),
    "vercel": ("Vercel", ("VERCEL_TOKEN",)),
    "fly": ("Fly", ("FLY_API_TOKEN",)),
    "flyctl": ("Fly", ("FLY_API_TOKEN",)),
    "neonctl": ("Neon", ("NEON_API_KEY",)),
}
RAILS = {"rails", "rails-staging", "worker-staging", "postgres", "postgres-staging"}
RAILS_FLOOR = (
    "kept floor: no acting in Alex's account; raw railway ssh, run or shell into the Rails services "
    "is refused. Use scripts/owner_admin.py or scripts/owner_admin_move.py with a test account"
)
PROVIDER_APPROVAL = re.compile(r"\s+# alex-approval:\s*(\S+)\s*$")
HEREDOC_OPEN = re.compile(r"<<-?\s*(?:'([^']+)'|\"([^\"]+)\"|([\w]+))")
FACTORY_TOKEN = re.compile(r"\$\(\s*agent-secret\s+get\s+railway-factory-prod-token\s*\)")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=")
VARIABLE_REF = re.compile(r"\$\{?([A-Za-z_][A-Za-z_0-9]*)\}?")
RAILWAY_CLI = re.compile(r"(?:@railway/cli|railway)(?:@[^/]+)?")
RAILWAY_WORD = re.compile(r"\brailway\b")
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
    return bool(m) and checked(m[1], "destructive")


def checked(ref, action, line=None):
    reason = approval_record.refusal(ref, action, line)
    if reason:
        sys.stderr.write("approval record refused: " + reason + "\n")
    return not reason


def provider_approved(text):
    match = PROVIDER_APPROVAL.search(text)
    if not match or RAILWAY_WORD.search(text):
        return False
    return not approval_record.refusal(match[1], "provider", text[: match.start()].strip())


def rails_target(words, text, cwd):
    if len(words) < 2 or words[1] not in ("ssh", "run", "shell"):
        return False
    for i, word in enumerate(words):
        if word.startswith("--service="):
            return word.split("=", 1)[1] in RAILS
        if word in ("-s", "--service"):
            return i + 1 < len(words) and words[i + 1] in RAILS
    return "agent-rails" in re.sub(r"\S*\.agent-rails/approvals\S*", "", cwd + " " + text)


def without_heredocs(source):
    clean = []
    delimiters = []
    for line in source.splitlines(keepends=True):
        if delimiters:
            if line.rstrip("\r\n").lstrip("\t") == delimiters[0]:
                delimiters.pop(0)
            continue
        clean.append(line)
        delimiters.extend(m[1] or m[2] or m[3] for m in HEREDOC_OPEN.finditer(line))
    return "".join(clean)


def substitutions(source):
    quote = None
    i = 0
    while i < len(source):
        char = source[i]
        if char == "\\" and quote != "'":
            i += 2
            continue
        if char == "'" and quote != '"':
            quote = None if quote else char
        elif char == '"' and quote != "'":
            quote = None if quote else char
        elif quote != "'" and source.startswith("$(", i) and not source.startswith("$((", i):
            start = i + 2
            level = 1
            i = start
            while i < len(source) and level:
                if source[i] == "(":
                    level += 1
                elif source[i] == ")":
                    level -= 1
                i += 1
            if level:
                raise ValueError("unclosed substitution")
            yield source[start : i - 1]
            continue
        elif quote != "'" and char == "`":
            end = source.find("`", i + 1)
            if end < 0:
                raise ValueError("unclosed substitution")
            yield source[i + 1 : end]
            i = end
        i += 1


def segments(source):
    lexer = shlex.shlex(source, posix=True, punctuation_chars=";&|()<>\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = "#"
    words = []
    for word in lexer:
        if all(c in ";&|()<>\n" for c in word):
            if words:
                yield words
                words = []
        else:
            words.append(word)
    if words:
        yield words


def strip_prefixes(words, exported, local):
    while words and (ASSIGNMENT.match(words[0]) or words[0] in ("env", "command", "exec")):
        word = words.pop(0)
        if word == "env":
            while words and words[0].startswith("-"):
                option = words.pop(0)
                if option in ("-i", "--ignore-environment"):
                    local.clear()
                elif option in ("-u", "--unset") and words:
                    local.pop(words.pop(0), None)
                elif option.startswith("--unset="):
                    local.pop(option.split("=", 1)[1], None)
                elif option == "--":
                    break
        elif word in ("command", "exec"):
            while words and words[0].startswith("-"):
                words.pop(0)
        if "=" in word:
            key, value = word.split("=", 1)
            ref = VARIABLE_REF.fullmatch(value)
            local[key] = exported.get(ref[1], "") if ref else value


def provider_verdict(words, local, text, cwd, depth):
    name = os.path.basename(words[0])
    if name in ("bash", "sh", "zsh"):
        for i, word in enumerate(words[1:], 1):
            if word.startswith("-") and "c" in word[1:] and i + 1 < len(words):
                result = inspect(words[i + 1], local, text, cwd, depth + 1)
                if result:
                    return result
                break
    if name in ("npx", "bunx"):
        args = words[1:]
        while args and args[0] in ("--yes", "-y", "--no-install", "--bun", "--"):
            args.pop(0)
        if args and RAILWAY_CLI.fullmatch(args[0]):
            words = ["railway", *args[1:]]
            name = "railway"
    if name == "railway" and (
        words[1:2] in (["--help"], ["-h"])
        or words[2:3] in (["--help"], ["-h"])
        or words[1:] in (["--version"], ["-V"])
        or words[1:2] == ["help"]
    ):
        return ""
    if name == "railway" and words[1:2] == ["login"]:
        return "kept floor: Alex's own Railway login. Use a factory-provisioned RAILWAY_TOKEN"
    if name == "railway" and rails_target(words, text, cwd):
        return RAILS_FLOOR
    if name in PROVIDERS:
        label, keys = PROVIDERS[name]
        if not any(local.get(key) for key in keys):
            return "kept floor: Alex's own %s login. Use a factory-provisioned %s (ask ub_vaxptt)" % (label, keys[0])
    return ""


def inspect(source, inherited, text, cwd, depth=0):
    if depth > 12:
        raise ValueError("shell nesting limit")
    source = without_heredocs(source)
    for body in substitutions(source):
        result = inspect(body, inherited, text, cwd, depth + 1)
        if result:
            return result
    source = FACTORY_TOKEN.sub("FACTORY_PROJECT_TOKEN", source)
    exported = dict(inherited)
    for words in segments(source):
        local = dict(exported)
        strip_prefixes(words, exported, local)
        if not words:
            continue
        name = os.path.basename(words[0])
        if name == "export":
            for word in words[1:]:
                if "=" in word:
                    key, value = word.split("=", 1)
                    exported[key] = value
            continue
        if name == "unset":
            for key in words[1:]:
                exported.pop(key, None)
            continue
        result = provider_verdict(words, local, text, cwd, depth)
        if result:
            return result
    return ""


def provider_block(text, cwd):
    if provider_approved(text):
        return ""
    environment = dict(os.environ, HOOK_CWD=cwd)
    try:
        return inspect(text, environment, text, cwd)
    except ValueError:
        sys.stderr.write("WARNING: provider guard could not parse executable argv; allowing command.\n")
        return ""


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
        got, inherits, wrote = psql_argv.inspect(args, read, inline, unread, lambda w: psql_commands.body(w, bodies))
        found += got
        writes += wrote
        inherit |= inherits
    if inherit and feeds:
        unread.append("%s: its stdin is fed through %s" % (psql_argv.FEEDS, " ".join(dict.fromkeys(feeds))))
    unread += ["%s (written in the same command)" % word for word in psql_argv.touches(writes, cwds, loaded)]
    return [piece.replace("\x00", "") for piece in found], unread


def main():
    cmd, cwd = read_hook_input()
    if not cmd:
        allow()
    try:
        block = provider_block(cmd, cwd)
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
