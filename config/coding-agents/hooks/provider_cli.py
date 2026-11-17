import os
import re
import shlex
import sys

import approval_record  # type: ignore[missing-import]

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
