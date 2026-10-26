#!/bin/bash
IFS= read -r -d '' GUARD <<'PY'
import json
import os
import re
import shlex
import sys

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
SQL_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z_0-9]*)\1")
SHELL_LINE = re.compile(r"\s*(?:bash|sh|zsh)\b")
FACTORY_TOKEN = re.compile(r"\$\(\s*agent-secret\s+get\s+railway-factory-prod-token\s*\)")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=")
VARIABLE_REF = re.compile(r"\$\{?([A-Za-z_][A-Za-z_0-9]*)\}?")
RAILWAY_CLI = re.compile(r"(?:@railway/cli|railway)(?:@[^/]+)?")
RAILWAY_WORD = re.compile(r"\brailway\b")
UNPARSED_FLOOR = re.compile(r"DROP|TRUNCATE|launchctl[^\S\n]+disable", re.I)
DESTRUCTIVE_SQL = re.compile(
    r"(DROP[^\S\n]+(DATABASE|TABLE)|TRUNCATE[^\S\n]+(TABLE[^\S\n]+)?[A-Za-z_])", re.I
)
REF_COMMENT = re.compile(r"#\s*alex-approval:\s*(\S+)\s*")
REF_NAME = re.compile(r"alex-.+-2026-.+\.md")
REF_TIME = re.compile(r"\b\d{1,2}:\d{2}\b")
HOME_PREFIX = re.compile(r"^(?:~|\$HOME|\$\{HOME\})/")


def on_path(name):
    for folder in os.environ.get("PATH", os.defpath).split(os.pathsep):
        candidate = os.path.join(folder or ".", name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return True
    return False


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
    if not m:
        return False
    home = os.path.expanduser("~")
    path = HOME_PREFIX.sub(home + "/", m[1])
    refs = home + "/.agent-rails/lanes/orchestrator-refs/"
    name = path[len(refs) :]
    if not path.startswith(refs) or ".." in path or "/" in name or not REF_NAME.fullmatch(name) or os.path.islink(path):
        return False
    try:
        with open(path) as f:
            return REF_TIME.search(f.read()) is not None
    except OSError:
        return False


def provider_approved(text):
    match = PROVIDER_APPROVAL.search(text)
    if not match or RAILWAY_WORD.search(text):
        return False
    home = os.path.expanduser("~")
    path = os.path.expanduser(match[1].replace("${HOME}", home).replace("$HOME", home))
    root = os.path.expanduser("~/.open-factory/approvals")
    if os.path.dirname(path) != root or not re.fullmatch(r"alex-.*\.md", os.path.basename(path)) or os.path.islink(path):
        return False
    try:
        with open(path) as handle:
            approval = handle.read()
    except OSError:
        return False
    exact = text[: match.start()].strip()
    return any(line.strip().strip("`") == exact for line in approval.splitlines())


def rails_target(words, text, cwd):
    if len(words) < 2 or words[1] not in ("ssh", "run", "shell"):
        return False
    for i, word in enumerate(words):
        if word.startswith("--service="):
            return word.split("=", 1)[1] in RAILS
        if word in ("-s", "--service"):
            return i + 1 < len(words) and words[i + 1] in RAILS
    return "agent-rails" in cwd + " " + text


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


def commands(text):
    lines = text.splitlines(keepends=True)
    clean = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = SQL_HEREDOC.search(line)
        clean.append(line)
        i += 1
        if m:
            body = []
            while i < len(lines) and lines[i].strip() != m[2]:
                body.append(lines[i])
                i += 1
            i += 1
            if SHELL_LINE.match(line):
                yield from commands("".join(body))
    lexer = shlex.shlex("".join(clean), posix=True, punctuation_chars=";|&<>")
    lexer.whitespace = " \t\r"
    lexer.commenters = "#"
    segment = []
    for token in lexer:
        if token == "\n" or token in (";", "&&", "||", "|", "&"):
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
            raise OSError("larger than limit")
        with open(path) as f:
            return f.read(1_000_000)
    except (OSError, UnicodeError):
        sys.stderr.write("dangerous-command-guard: SQL input unreadable or >=1 MB; passing without inspection.\n")
        return ""


def sql_text(cmd):
    found = []
    parts = list(commands(cmd))
    for index, words in enumerate(parts):
        name = os.path.basename(words[0])
        if name in ("bash", "sh", "zsh") and "-c" in words:
            at = words.index("-c")
            if at + 1 < len(words):
                parts.extend(commands(words[at + 1]))
        if name != "psql":
            continue
        for i, word in enumerate(words[1:], 1):
            if word in ("-c", "--command") and i + 1 < len(words):
                found.append(words[i + 1])
            elif word.startswith("--command="):
                found.append(word.split("=", 1)[1])
            elif word in ("-f", "--file", "<") and i + 1 < len(words):
                found.append(read_file(words[i + 1]))
            elif word.startswith("--file="):
                found.append(read_file(word.split("=", 1)[1]))
        if index and parts[index - 1][0] == "cat" and len(parts[index - 1]) == 2:
            found.append(read_file(parts[index - 1][1]))
    return "\n".join(found).replace("\x00", "")


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
        sql = sql_text(cmd)
    except Exception:
        if on_path("grep") and UNPARSED_FLOOR.search(cmd):
            refuse("could not parse a kept-floor command")
        sys.stderr.write("WARNING: destructive-data guard could not parse executable argv; allowing command.\n")
        allow()
    if not on_path("grep"):
        refuse("could not match the command (grep exit 127: missing or failed)")
    if DESTRUCTIVE_SQL.search(sql) and not approved(cmd):
        sys.stderr.write(
            "BLOCKED: Dangerous command requires explicit user approval (%s). Approved: append "
            "' # alex-approval: ~/.agent-rails/lanes/orchestrator-refs/alex-<topic>-2026-<date>.md' "
            "(a first-hand Alex quote with a time).\n" % FLOOR
        )
        sys.exit(2)
    allow()


main()
PY
shopt -s execfail
exec python3 -I -S -c "$GUARD" "$@"
echo "BLOCKED: dangerous-command-guard could not start python3, so this call is refused (the guard fails closed)." >&2
exit 2
